"""The Diary's summary of a trade, written when an import closes it.

Summaries are written after the import has answered, so a slow or failed AI call never holds
one up. A failure leaves a button to try again. To keep a history import from running up API
costs, only recent closes get a summary by themselves, and only a few per import.
"""
import json
import re
import sqlite3
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from ai_analysis import MODEL, DIARY_MAX_TOKENS, get_client, response_text
from database import get_db, row_to_dict
from trade_comments import fill_key
from trade_plans import _is_closed, _opening_fill
from trade_review import _fingerprint, trade_facts

router = APIRouter(prefix="/api")

RECENT_CLOSE_DAYS = 30
MAX_WRITTEN_PER_IMPORT = 5

SUMMARY_PROMPT = """You write the journal entry for one finished swing trade, in the second person, for the trader who \
took it. You receive JSON with the trade's written plan (if any), its fills with the position after each, the trader's \
comments and notes, and daily price bars.

Rules:
- Tell the trade in order: the idea, the entry, what was done along the way and why, the exit, and the result.
- Use actual dates, prices and dollar amounts from the data. Do not invent reasons, levels or indicators. If the trader recorded no reason for a decision, say so.
- Judge each decision only on what was known that day. Bars after a fill are hindsight, and may be used only to point out what happened next.
- Fill times are US Eastern. If price_bars is empty, do not comment on price action.
- Be brief. Return ONLY valid JSON, with no markdown fences.

Required JSON schema:
{
  "headline": "One line, under 12 words",
  "story": "One or two short paragraphs",
  "against_plan": "How the trade compared with its plan. If there is no plan, say that nothing was written down to compare with.",
  "lessons": ["specific lesson for the next trade"]
}"""


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_trade_summaries_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            trade_group TEXT NOT NULL,
            opening_fill_key TEXT,
            close_date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','writing','ready','failed')),
            content TEXT,
            input_hash TEXT,
            error TEXT,
            model TEXT,
            generated_at TEXT,
            UNIQUE(account_id, trade_group)
        )
    """)
    # Anything still writing when the app stopped will never finish.
    conn.execute("UPDATE trade_summaries SET status='failed', error='The app stopped while this was being written' "
                 "WHERE status='writing'")
    conn.commit()


def delete_trade_summary(conn, account_id: int, trade_group: str) -> None:
    conn.execute("DELETE FROM trade_summaries WHERE account_id=? AND trade_group=?", (account_id, trade_group))


def _last_fill(trade) -> dict | None:
    execs = json.loads(trade['executions'] or '[]')
    return max(execs, key=lambda e: (e.get('date', ''), e.get('time', ''))) if execs else None


def _set(conn, trade, status: str, **values) -> None:
    last, opening = _last_fill(trade), _opening_fill(trade['executions'])
    row = {'content': None, 'input_hash': None, 'error': None, 'model': None, 'generated_at': None, **values}
    conn.execute(
        """INSERT INTO trade_summaries (account_id, trade_group, opening_fill_key, close_date, status,
                                        content, input_hash, error, model, generated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(account_id, trade_group) DO UPDATE SET
             opening_fill_key=excluded.opening_fill_key, close_date=excluded.close_date, status=excluded.status,
             content=COALESCE(excluded.content, content), input_hash=COALESCE(excluded.input_hash, input_hash),
             error=excluded.error, model=COALESCE(excluded.model, model),
             generated_at=COALESCE(excluded.generated_at, generated_at)""",
        (trade['account_id'], trade['trade_group'], fill_key(opening) if opening else None,
         last['date'] if last else '', status, row['content'], row['input_hash'], row['error'], row['model'],
         row['generated_at']),
    )
    conn.commit()


def queue_summaries(conn, account_id: int, before: set, today: date) -> list[str]:
    """Trades this import closed get a Diary row. Returns the ones to write now, newest first."""
    closes = []
    for trade in conn.execute("SELECT * FROM trades WHERE account_id=?", (account_id,)).fetchall():
        last = _last_fill(trade)
        if not last or not _is_closed(trade) or (trade['ticker'], fill_key(last)) in before:
            continue
        if conn.execute("SELECT 1 FROM trade_summaries WHERE account_id=? AND trade_group=?",
                        (account_id, trade['trade_group'])).fetchone():
            continue
        if last['date'] >= (today - timedelta(days=RECENT_CLOSE_DAYS)).isoformat():
            closes.append((last['date'], trade))
    closes.sort(key=lambda c: c[0], reverse=True)
    write_now = []
    for i, (_, trade) in enumerate(closes):
        if i < MAX_WRITTEN_PER_IMPORT:
            _set(conn, trade, 'writing')
            write_now.append(trade['trade_group'])
        else:
            _set(conn, trade, 'pending')
    return write_now


def generate_summary(facts: dict, bars: list[dict], today: str) -> dict:
    client = get_client()
    price_bars = [{'date': (b.get('t') or '')[:10], 'o': b.get('o'), 'h': b.get('h'), 'l': b.get('l'), 'c': b.get('c'), 'v': b.get('v')}
                  for b in bars]
    response = client.messages.create(
        model=MODEL,
        max_tokens=DIARY_MAX_TOKENS,
        system=SUMMARY_PROMPT,
        messages=[{"role": "user", "content": json.dumps({'today': today, **facts, 'price_bars': price_bars}, default=str)}],
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ValueError("The response was cut off before it finished")
    raw = response_text(response)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    result = json.loads(raw)
    for key, empty in (("headline", ""), ("story", ""), ("against_plan", ""), ("lessons", [])):
        result.setdefault(key, empty)
    return result


def mark_writing(conn, trade) -> None:
    _set(conn, trade, 'writing')


def mark_ready(conn, trade, facts: dict, content: dict) -> None:
    _set(conn, trade, 'ready', content=json.dumps(content), input_hash=_fingerprint(facts), model=MODEL,
         generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))


def mark_failed(conn, trade, error: Exception) -> None:
    text = str(error)
    if "ANTHROPIC_API_KEY" in text:
        text = "Add ANTHROPIC_API_KEY to backend/.env to write summaries."
    _set(conn, trade, 'failed', error=text)


def summary_view(conn, trade, row) -> dict:
    out = row_to_dict(row)
    out['content'] = json.loads(row['content']) if row['content'] else None
    out['out_of_date'] = bool(row['input_hash']) and row['input_hash'] != _fingerprint(trade_facts(conn, trade))
    out['trade'] = row_to_dict(trade)
    return out


def summary_of(conn, trade) -> dict | None:
    row = conn.execute("SELECT * FROM trade_summaries WHERE account_id=? AND trade_group=?",
                       (trade['account_id'], trade['trade_group'])).fetchone()
    return summary_view(conn, trade, row) if row else None


def summary_dates(conn, account_id: int | None) -> set:
    sql = "SELECT close_date FROM trade_summaries"
    params = []
    if account_id is not None:
        sql += " WHERE account_id=?"
        params.append(account_id)
    return {r['close_date'] for r in conn.execute(sql, params)}


@router.get("/trade-summaries")
def list_summaries(account_id: int | None = None, conn: sqlite3.Connection = Depends(get_connection)):
    sql = "SELECT * FROM trade_summaries"
    params = []
    if account_id is not None:
        sql += " WHERE account_id=?"
        params.append(account_id)
    out = []
    for row in conn.execute(sql + " ORDER BY close_date DESC, id DESC", params).fetchall():
        trade = conn.execute("SELECT * FROM trades WHERE account_id=? AND trade_group=?",
                             (row['account_id'], row['trade_group'])).fetchone()
        if trade:
            out.append(summary_view(conn, trade, row))
    return out


@router.delete("/trade-summaries/{summary_id:int}")
def delete_summary(summary_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    conn.execute("DELETE FROM trade_summaries WHERE id=?", (summary_id,))
    conn.commit()
    return {"deleted": True, "id": summary_id}


@router.get("/trades/{trade_id:int}/summary")
def get_trade_summary(trade_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    trade = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"summary": summary_of(conn, trade)}

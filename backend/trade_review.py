"""AI feedback on one trade, shown beside its chart.

Run only when asked, since each run costs API credit. The result is saved with the trade and
follows it through regrouping. It is flagged as out of date when the plan, fills, comments or
notes it was based on change. Prices are left out of that check: new bars arrive every day.
"""
import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from ai_analysis import MODEL, DIARY_MAX_TOKENS, get_client, response_text
from database import get_db
from trade_comments import fill_key
from trade_plans import _is_closed, _opening_fill

router = APIRouter(prefix="/api")

BARS_BEFORE_ENTRY_DAYS = 60

ANALYSIS_COLUMNS = ("strategy", "idea_source", "stop_loss", "target_price", "risk_per_trade", "r_multiple",
                    "entry_reason", "exit_reason", "mistakes", "emotional_state", "notes")
PLAN_COLUMNS = ("plan_date", "direction", "idea_source", "setup_type", "timeframe", "higher_tf_trend", "key_level",
                "volume_momentum", "entry_trigger", "invalidation", "entry_price", "stop_price", "target_price",
                "shares", "account_size")

REVIEW_PROMPT = """You are a trading coach reviewing one swing trade from a trader's journal. You receive JSON with the \
trade's written plan (if any), its fills with the position after each, the trader's comments and notes, and daily price bars.

Rules:
- Judge the process, not the result: a loss that followed a sound plan can grade well, and a lucky win can grade badly.
- Judge each decision only on what was known that day. Bars after a fill are hindsight, and may be used only to point out what happened next.
- Compare what was done with the plan: entry against trigger, price and size; stop and target; whether the invalidation occurred and what the trader did then.
- Use actual dates, prices and dollar amounts from the data. Do not invent reasons, levels or indicators. If the trader recorded no reason for a decision, say so.
- Fill times are US Eastern. Volume may come from a partial feed, so treat it as relative only. If price_bars is empty, say so and do not comment on price action.
- If status is "open", next_step says what the plan says to watch from here. Do not predict the price or tell the trader to buy or sell. If status is "closed", next_step is the one lesson for the next trade.
- Be direct and brief. Return ONLY valid JSON, with no markdown fences.

Required JSON schema:
{
  "grade": "A|B|C|D|F",
  "summary": "2-3 sentences: what happened and the main judgement",
  "plan_check": "How the trade compared with its plan. If there is no plan, say that nothing was written down to compare with.",
  "went_well": ["specific point"],
  "improve": ["specific point"],
  "next_step": "One or two sentences"
}"""


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_trade_reviews_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            trade_group TEXT NOT NULL,
            opening_fill_key TEXT,
            content TEXT NOT NULL,
            input_hash TEXT NOT NULL,
            model TEXT,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(account_id, trade_group)
        )
    """)
    conn.commit()


def move_reviews_on_regroup(conn, account_id: int, trades: list[dict], old_groups: set, moves: dict) -> None:
    """Feedback stays with the fill that opened its trade. If the target already has its own, that one is kept."""
    if not old_groups:
        return
    marks = ",".join("?" for _ in old_groups)
    rows = conn.execute(
        f"SELECT id, trade_group, opening_fill_key FROM trade_reviews WHERE account_id=? AND trade_group IN ({marks})",
        [account_id, *old_groups],
    ).fetchall()
    for row in rows:
        old = row['trade_group']
        target = moves.get(old, old)
        for t in trades:
            if old in t.get('replaces', []) and row['opening_fill_key'] in {fill_key(e) for e in json.loads(t['executions'])}:
                target = t['trade_group']
                break
        if target != old:
            conn.execute("UPDATE OR IGNORE trade_reviews SET trade_group=? WHERE id=?", (target, row['id']))
            conn.execute("DELETE FROM trade_reviews WHERE id=? AND trade_group=?", (row['id'], old))


def delete_trade_review(conn, account_id: int, trade_group: str) -> None:
    conn.execute("DELETE FROM trade_reviews WHERE account_id=? AND trade_group=?", (account_id, trade_group))


def _fills_with_position(trade) -> list[dict]:
    """The fills in date order, with the shares held and average cost after each."""
    opens_with = 'SOLD' if trade['side'] == 'SHORT' else 'BOT'
    held = cost = 0.0
    out = []
    for e in sorted(json.loads(trade['executions'] or '[]'), key=lambda e: (e.get('date', ''), e.get('time', ''))):
        qty, price = float(e.get('qty') or 0), float(e.get('price') or 0)
        if e.get('action') == opens_with:
            cost = (cost * held + price * qty) / (held + qty) if held + qty else 0.0
            held += qty
        else:
            held -= qty
        out.append({'date': e.get('date'), 'time': e.get('time'), 'action': e.get('action'), 'qty': qty, 'price': price,
                    'shares_held_after': held, 'avg_cost_after': round(cost, 4), '_key': fill_key(e)})
    return out


def trade_facts(conn, trade) -> dict:
    """Everything the feedback is based on, apart from prices."""
    account_id, group = trade['account_id'], trade['trade_group']
    plan = conn.execute("SELECT * FROM trade_plans WHERE account_id=? AND trade_group=?", (account_id, group)).fetchone()
    analysis = conn.execute("SELECT * FROM trade_analysis WHERE trade_group=?", (group,)).fetchone()
    comments = conn.execute(
        "SELECT note_date, fill_key, body FROM trade_comments WHERE account_id=? AND trade_group=? ORDER BY note_date, id",
        (account_id, group),
    ).fetchall()
    fills = _fills_with_position(trade)
    by_key = {f['_key']: f for f in fills}
    notes = []
    for c in comments:
        fill = by_key.get(c['fill_key'])
        notes.append({'date': c['note_date'], 'on_fill': f"{fill['action']} {fill['qty']:g} @ {fill['price']:g}" if fill else None,
                      'text': c['body']})
    return {
        'ticker': trade['ticker'],
        'side': trade['side'],
        'instrument_type': trade['instrument_type'],
        'status': 'closed' if _is_closed(trade) else 'open',
        'net_pnl': trade['net_pnl'],
        'plan': {k: plan[k] for k in PLAN_COLUMNS} if plan else None,
        'fills': [{k: v for k, v in f.items() if k != '_key'} for f in fills],
        'comments': notes,
        'journal_fields': {k: analysis[k] for k in ANALYSIS_COLUMNS if analysis[k] not in (None, '')} if analysis else {},
    }


def _fingerprint(facts: dict) -> str:
    return hashlib.sha256(json.dumps(facts, sort_keys=True, default=str).encode()).hexdigest()


def bars_window(facts: dict, today: date) -> tuple[str, int]:
    """Daily bars from before the entry to the last fill, or to today while the trade is open."""
    dates = [f['date'] for f in facts['fills']]
    first = datetime.strptime(min(dates), "%Y-%m-%d").date() if dates else today
    last = datetime.strptime(max(dates), "%Y-%m-%d").date() if dates else today
    anchor = today if facts['status'] == 'open' else last
    start = first - timedelta(days=BARS_BEFORE_ENTRY_DAYS)
    return anchor.isoformat(), (anchor - start).days + 1


def generate_review(facts: dict, bars: list[dict], today: str) -> dict:
    client = get_client()
    price_bars = [{'date': (b.get('t') or '')[:10], 'o': b.get('o'), 'h': b.get('h'), 'l': b.get('l'), 'c': b.get('c'), 'v': b.get('v')}
                  for b in bars]
    response = client.messages.create(
        model=MODEL,
        max_tokens=DIARY_MAX_TOKENS,
        system=REVIEW_PROMPT,
        messages=[{"role": "user", "content": json.dumps({'today': today, **facts, 'price_bars': price_bars}, default=str)}],
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ValueError("The response was cut off before it finished")
    raw = response_text(response)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    result = json.loads(raw)
    for key, empty in (("grade", ""), ("summary", ""), ("plan_check", ""), ("went_well", []), ("improve", []), ("next_step", "")):
        result.setdefault(key, empty)
    return result


def save_review(conn, trade, facts: dict, content: dict) -> None:
    opening = _opening_fill(trade['executions'])
    conn.execute(
        """INSERT INTO trade_reviews (account_id, trade_group, opening_fill_key, content, input_hash, model)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(account_id, trade_group) DO UPDATE SET
             opening_fill_key=excluded.opening_fill_key, content=excluded.content, input_hash=excluded.input_hash,
             model=excluded.model, generated_at=datetime('now')""",
        (trade['account_id'], trade['trade_group'], fill_key(opening) if opening else None,
         json.dumps(content), _fingerprint(facts), MODEL),
    )
    conn.commit()


def load_review(conn, trade) -> dict:
    row = conn.execute("SELECT * FROM trade_reviews WHERE account_id=? AND trade_group=?",
                       (trade['account_id'], trade['trade_group'])).fetchone()
    if not row:
        return {'review': None}
    return {'review': json.loads(row['content']), 'generated_at': row['generated_at'],
            'out_of_date': row['input_hash'] != _fingerprint(trade_facts(conn, trade))}


@router.get("/trades/{trade_id:int}/review")
def get_trade_review(trade_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    trade = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not trade:
        raise HTTPException(status_code=404, detail="Trade not found")
    return load_review(conn, trade)

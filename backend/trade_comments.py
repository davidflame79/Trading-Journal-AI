"""Dated comments on a trade: why a fill happened, or a note with no fill behind it.

A trade's name (trade_group) and id can change when an import regroups its fills, so a
comment stores the fill it belongs to and is moved along with that fill. It lives in its own
table because diary uploads overwrite the analysis fields of an existing trade.
"""
import json
import re
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db, row_to_dict

router = APIRouter(prefix="/api")


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_trade_comments_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            trade_group TEXT NOT NULL,
            note_date TEXT NOT NULL,
            fill_key TEXT,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_comments_group ON trade_comments(account_id, trade_group)")
    conn.commit()


def fill_key(e: dict) -> str:
    """Identifies a fill within a trade. frontend/src/components/TradeTimeline.js builds the same string."""
    return (f"{e.get('date', '')}|{e.get('time', '')}|{e.get('action', '')}"
            f"|{float(e.get('qty') or 0):.6f}|{float(e.get('price') or 0):.6f}")


def _execs(raw) -> list[dict]:
    return json.loads(raw or '[]')


def move_comments_on_regroup(conn, account_id: int, trades: list[dict], old_groups: set, moves: dict) -> None:
    """Comments follow their fill to whichever new trade holds it. A note with no fill, or
    whose fill is gone, stays with its trade, or goes where that trade's fills mostly went."""
    if not old_groups:
        return
    marks = ",".join("?" for _ in old_groups)
    rows = conn.execute(
        f"SELECT id, trade_group, fill_key FROM trade_comments WHERE account_id=? AND trade_group IN ({marks})",
        [account_id, *old_groups],
    ).fetchall()
    holders = {t['trade_group']: {fill_key(e) for e in _execs(t['executions'])} for t in trades}
    for row in rows:
        old = row['trade_group']
        target = moves.get(old, old)
        if row['fill_key']:
            for t in trades:
                if old in t.get('replaces', []) and row['fill_key'] in holders[t['trade_group']]:
                    target = t['trade_group']
                    break
        if target != old:
            conn.execute("UPDATE trade_comments SET trade_group=? WHERE id=?", (target, row['id']))


def rekey_fill(conn, account_id: int, trade_group: str, old: dict, new: dict | None) -> None:
    """Keep a comment on a fill that was edited by hand. A deleted fill leaves a dated note."""
    new_key = fill_key(new) if new else None
    conn.execute(
        "UPDATE trade_comments SET fill_key=?, note_date=COALESCE(?, note_date) "
        "WHERE account_id=? AND trade_group=? AND fill_key=?",
        (new_key, new.get('date') if new else None, account_id, trade_group, fill_key(old)),
    )


def delete_trade_comments(conn, account_id: int, trade_group: str) -> None:
    conn.execute("DELETE FROM trade_comments WHERE account_id=? AND trade_group=?", (account_id, trade_group))


def stored_fills(conn, account_id: int) -> set:
    """Every fill stored for an account, taken before an import to spot the new ones."""
    rows = conn.execute("SELECT ticker, executions FROM trades WHERE account_id=?", (account_id,)).fetchall()
    return {(r['ticker'], fill_key(e)) for r in rows for e in _execs(r['executions'])}


def trades_needing_comments(conn, account_id: int, before: set) -> list[dict]:
    """Trades holding fills that were not stored before this import and have no comment yet."""
    commented = {(r['trade_group'], r['fill_key']) for r in conn.execute(
        "SELECT trade_group, fill_key FROM trade_comments WHERE account_id=? AND fill_key IS NOT NULL", (account_id,))}
    out = []
    for row in conn.execute("SELECT * FROM trades WHERE account_id=? ORDER BY date, id", (account_id,)).fetchall():
        keys = [fill_key(e) for e in _execs(row['executions'])]
        missing = sum(1 for k in keys
                      if (row['ticker'], k) not in before and (row['trade_group'], k) not in commented)
        if missing:
            out.append({**row_to_dict(row), 'new_fill_count': missing})
    return out


class CommentCreate(BaseModel):
    body: str
    note_date: str | None = None
    fill_key: str | None = None


class CommentUpdate(BaseModel):
    body: str | None = None
    note_date: str | None = None


def _clean_body(body: str) -> str:
    text = (body or '').strip()
    if not text:
        raise HTTPException(status_code=400, detail="A comment cannot be empty")
    return text


def _check_date(value: str) -> str:
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', value or ''):
        raise HTTPException(status_code=400, detail="note_date must be YYYY-MM-DD")
    return value


def _get_trade(conn, trade_id: int):
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")
    return row


def _get_comment(conn, comment_id: int) -> dict:
    row = conn.execute("SELECT * FROM trade_comments WHERE id=?", (comment_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Comment not found")
    return row_to_dict(row)


@router.get("/trades/{trade_id:int}/comments")
def list_comments(trade_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    trade = _get_trade(conn, trade_id)
    rows = conn.execute(
        "SELECT * FROM trade_comments WHERE account_id=? AND trade_group=? ORDER BY note_date, id",
        (trade['account_id'], trade['trade_group']),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


@router.post("/trades/{trade_id:int}/comments", status_code=201)
def create_comment(trade_id: int, data: CommentCreate, conn: sqlite3.Connection = Depends(get_connection)):
    trade = _get_trade(conn, trade_id)
    body = _clean_body(data.body)
    note_date = data.note_date
    if data.fill_key:
        fill = next((e for e in _execs(trade['executions']) if fill_key(e) == data.fill_key), None)
        if not fill:
            raise HTTPException(status_code=400, detail="That fill is not part of this trade")
        note_date = note_date or fill.get('date')
    cur = conn.execute(
        "INSERT INTO trade_comments (account_id, trade_group, note_date, fill_key, body) VALUES (?,?,?,?,?)",
        (trade['account_id'], trade['trade_group'], _check_date(note_date), data.fill_key or None, body),
    )
    conn.commit()
    return _get_comment(conn, cur.lastrowid)


@router.patch("/trade-comments/{comment_id:int}")
def update_comment(comment_id: int, data: CommentUpdate, conn: sqlite3.Connection = Depends(get_connection)):
    comment = _get_comment(conn, comment_id)
    body = comment['body'] if data.body is None else _clean_body(data.body)
    note_date = comment['note_date'] if data.note_date is None else _check_date(data.note_date)
    conn.execute(
        "UPDATE trade_comments SET body=?, note_date=?, updated_at=datetime('now') WHERE id=?",
        (body, note_date, comment_id),
    )
    conn.commit()
    return _get_comment(conn, comment_id)


@router.delete("/trade-comments/{comment_id:int}")
def delete_comment(comment_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    _get_comment(conn, comment_id)
    conn.execute("DELETE FROM trade_comments WHERE id=?", (comment_id,))
    conn.commit()
    return {"deleted": True, "id": comment_id}

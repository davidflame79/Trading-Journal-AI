"""Trade plans: the setup written before entering, attached to the trade it became.

A plan belongs to one trade at most. Rebuying after going flat is a new trade with a new
plan, so an attached plan is never offered again. Attaching fills the trade's empty strategy,
source, stop, target and entry reason, so the existing stats and chart lines work from it.
"""
import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import get_db, row_to_dict
from trade_comments import fill_key

router = APIRouter(prefix="/api")

# trade_analysis column -> plan column it is filled from
ANALYSIS_FIELDS = {
    "strategy": "setup_type",
    "idea_source": "idea_source",
    "stop_loss": "stop_price",
    "target_price": "target_price",
    "entry_reason": "entry_trigger",
}

# What the plan form's dropdowns offer until changed in Settings > General.
PLAN_OPTION_DEFAULTS = {
    "idea_sources": ["Scanner", "Didi", "News/Catalyst", "Chart Pattern", "Analyst Call", "Earnings", "Other"],
    "setup_types": ["Breakout", "Pullback / Retracement", "Trend Continuation", "Reversal",
                    "Support / Resistance Bounce", "Range"],
    "higher_tf_trends": ["Uptrend", "Downtrend", "Range / Choppy"],
}


def get_connection():
    conn = get_db()
    try:
        yield conn
    finally:
        conn.close()


def init_trade_plans_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','active','cancelled')),
            plan_date TEXT,
            idea_source TEXT,
            setup_type TEXT,
            timeframe TEXT,
            higher_tf_trend TEXT,
            key_level TEXT,
            volume_momentum TEXT,
            entry_trigger TEXT,
            invalidation TEXT,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            shares REAL,
            account_size REAL,
            trade_group TEXT,
            opening_fill_key TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_plans_group ON trade_plans(account_id, trade_group)")
    conn.commit()


def _execs(raw) -> list[dict]:
    return json.loads(raw or '[]')


def _opening_fill(executions) -> dict | None:
    execs = _execs(executions)
    return min(execs, key=lambda e: (e.get('date', ''), e.get('time', ''))) if execs else None


def _is_closed(trade) -> bool:
    execs = _execs(trade['executions'])
    bought = sum(e.get('qty', 0) for e in execs if e.get('action') == 'BOT')
    sold = sum(e.get('qty', 0) for e in execs if e.get('action') == 'SOLD')
    return bool(execs) and bought == sold


def move_plans_on_regroup(conn, account_id: int, trades: list[dict], old_groups: set, moves: dict) -> None:
    """A plan stays with the fill that opened its trade when an import renames or splits it."""
    if not old_groups:
        return
    marks = ",".join("?" for _ in old_groups)
    rows = conn.execute(
        f"SELECT id, trade_group, opening_fill_key FROM trade_plans WHERE account_id=? AND trade_group IN ({marks})",
        [account_id, *old_groups],
    ).fetchall()
    for row in rows:
        old = row['trade_group']
        target = moves.get(old, old)
        for t in trades:
            if old in t.get('replaces', []) and row['opening_fill_key'] in {fill_key(e) for e in _execs(t['executions'])}:
                target = t['trade_group']
                break
        if target != old:
            conn.execute("UPDATE trade_plans SET trade_group=? WHERE id=?", (target, row['id']))


def rekey_opening_fill(conn, account_id: int, trade_group: str, old: dict, new: dict | None) -> None:
    """Keep a plan attached when its opening fill is edited by hand."""
    if new:
        conn.execute(
            "UPDATE trade_plans SET opening_fill_key=? WHERE account_id=? AND trade_group=? AND opening_fill_key=?",
            (fill_key(new), account_id, trade_group, fill_key(old)),
        )


def release_plan(conn, account_id: int, trade_group: str) -> None:
    """Deleting a trade must not lose the plan: it goes back to planned, ready for a re-import."""
    conn.execute(
        "UPDATE trade_plans SET status='planned', trade_group=NULL, opening_fill_key=NULL "
        "WHERE account_id=? AND trade_group=?",
        (account_id, trade_group),
    )


def _open_plans(conn, account_id: int, symbol: str, direction: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM trade_plans WHERE account_id=? AND symbol=? AND direction=? AND status='planned' "
        "ORDER BY plan_date DESC, id DESC",
        (account_id, (symbol or '').upper(), direction),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def plans_for_new_trades(conn, account_id: int, before: set) -> list[dict]:
    """Trades opened by this import, with the planned setups they could be attached to."""
    out = []
    attached = {r[0] for r in conn.execute(
        "SELECT trade_group FROM trade_plans WHERE account_id=? AND trade_group IS NOT NULL", (account_id,))}
    for row in conn.execute("SELECT * FROM trades WHERE account_id=? ORDER BY date, id", (account_id,)).fetchall():
        opening = _opening_fill(row['executions'])
        if not opening or row['trade_group'] in attached or (row['ticker'], fill_key(opening)) in before:
            continue
        plans = _open_plans(conn, account_id, row['ticker'], row['side'])
        if plans:
            out.append({'trade': row_to_dict(row), 'plans': plans})
    return out


def _fill_analysis(conn, trade, plan: dict) -> None:
    if not conn.execute("SELECT 1 FROM trade_analysis WHERE trade_group=?", (trade['trade_group'],)).fetchone():
        conn.execute("INSERT INTO trade_analysis (trade_group, ticker, date) VALUES (?,?,?)",
                     (trade['trade_group'], trade['ticker'], trade['date']))
    for column, source in ANALYSIS_FIELDS.items():
        conn.execute(
            f"UPDATE trade_analysis SET {column}=COALESCE(NULLIF({column},''), ?) WHERE trade_group=?",
            (plan[source], trade['trade_group']),
        )


def _change_analysis(conn, trade_group: str, old: dict, new: dict | None) -> None:
    """Pass a plan edit on to the trade, or clear it on detach, leaving values changed by hand."""
    for column, source in ANALYSIS_FIELDS.items():
        conn.execute(
            f"UPDATE trade_analysis SET {column}=? WHERE trade_group=? AND {column} IS ?",
            (new[source] if new else None, trade_group, old[source]),
        )


class PlanOptionsBody(BaseModel):
    idea_sources: list[str]
    setup_types: list[str]
    higher_tf_trends: list[str]


def _tidy_options(names: list[str]) -> list[str]:
    """Trim, drop blanks and repeats (ignoring case), and keep the trader's order."""
    seen = set()
    out = []
    for name in (n.strip() for n in names):
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
    return out


class PlanBody(BaseModel):
    account_id: int
    symbol: str
    direction: str = "LONG"
    status: str | None = None
    plan_date: str | None = None
    idea_source: str | None = None
    setup_type: str | None = None
    timeframe: str | None = None
    higher_tf_trend: str | None = None
    key_level: str | None = None
    volume_momentum: str | None = None
    entry_trigger: str | None = None
    invalidation: str | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    shares: float | None = None
    account_size: float | None = None


class StatusBody(BaseModel):
    status: str


class AttachBody(BaseModel):
    plan_id: int


def _clean(data: PlanBody, conn) -> dict:
    values = data.model_dump()
    values['symbol'] = values['symbol'].strip().upper()
    values['direction'] = values['direction'].strip().upper()
    if not values['symbol']:
        raise HTTPException(status_code=400, detail="A plan needs a symbol")
    if values['direction'] not in ('LONG', 'SHORT'):
        raise HTTPException(status_code=400, detail="Direction must be LONG or SHORT")
    if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (values['account_id'],)).fetchone():
        raise HTTPException(status_code=400, detail="Account not found")
    # Filled and closed follow the trade, so only these two can be chosen. Left out, it stays as it is.
    if values['status'] is None:
        del values['status']
    elif values['status'] not in ('planned', 'cancelled'):
        raise HTTPException(status_code=400, detail="Status must be planned or cancelled")
    return {k: (v.strip() or None) if isinstance(v, str) else v for k, v in values.items()}


def _get_plan(conn, plan_id: int) -> dict:
    row = conn.execute("SELECT * FROM trade_plans WHERE id=?", (plan_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Plan not found")
    return row_to_dict(row)


def _get_trade(conn, trade_id: int):
    row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Trade not found")
    return row


def _with_trade(conn, plan: dict) -> dict:
    """Add the attached trade, so the Plans page can link to it and show it as closed."""
    trade = None
    if plan['trade_group']:
        trade = conn.execute("SELECT * FROM trades WHERE account_id=? AND trade_group=?",
                             (plan['account_id'], plan['trade_group'])).fetchone()
    return {**plan, 'trade': row_to_dict(trade) if trade else None, 'closed': bool(trade) and _is_closed(trade)}


@router.get("/plans/options")
def get_plan_options(conn: sqlite3.Connection = Depends(get_connection)):
    row = conn.execute("SELECT value FROM settings WHERE account_id = 0 AND key = 'plan_options'").fetchone()
    return {**PLAN_OPTION_DEFAULTS, **json.loads(row['value'])} if row else PLAN_OPTION_DEFAULTS


@router.put("/plans/options")
def put_plan_options(data: PlanOptionsBody, conn: sqlite3.Connection = Depends(get_connection)):
    """Changing a list leaves plans already written as they are."""
    options = {k: _tidy_options(v) for k, v in data.model_dump().items()}
    conn.execute(
        """INSERT INTO settings (account_id, key, value) VALUES (0, 'plan_options', ?)
           ON CONFLICT(account_id, key) DO UPDATE SET value = excluded.value""",
        (json.dumps(options),),
    )
    conn.commit()
    return options


@router.get("/plans")
def list_plans(account_id: int | None = None, conn: sqlite3.Connection = Depends(get_connection)):
    sql = "SELECT * FROM trade_plans"
    params = []
    if account_id is not None:
        sql += " WHERE account_id=?"
        params.append(account_id)
    rows = conn.execute(sql + " ORDER BY COALESCE(plan_date, created_at) DESC, id DESC", params).fetchall()
    return [_with_trade(conn, row_to_dict(r)) for r in rows]


@router.post("/plans", status_code=201)
def create_plan(data: PlanBody, conn: sqlite3.Connection = Depends(get_connection)):
    values = _clean(data, conn)
    columns = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO trade_plans ({columns}) VALUES ({marks})", list(values.values()))
    conn.commit()
    return _get_plan(conn, cur.lastrowid)


@router.put("/plans/{plan_id:int}")
def update_plan(plan_id: int, data: PlanBody, conn: sqlite3.Connection = Depends(get_connection)):
    old = _get_plan(conn, plan_id)
    values = _clean(data, conn)
    if old['trade_group'] and any(values[k] != old[k] for k in ('account_id', 'symbol', 'direction')):
        raise HTTPException(status_code=400, detail="Detach the plan before changing its account, symbol or direction")
    if old['trade_group'] and 'status' in values:
        raise HTTPException(status_code=400, detail="Detach the plan from its trade first")
    set_clause = ", ".join(f"{k}=?" for k in values)
    conn.execute(f"UPDATE trade_plans SET {set_clause}, updated_at=datetime('now') WHERE id=?",
                 [*values.values(), plan_id])
    if old['trade_group']:
        _change_analysis(conn, old['trade_group'], old, _get_plan(conn, plan_id))
    conn.commit()
    return _get_plan(conn, plan_id)


@router.patch("/plans/{plan_id:int}/status")
def set_plan_status(plan_id: int, data: StatusBody, conn: sqlite3.Connection = Depends(get_connection)):
    plan = _get_plan(conn, plan_id)
    if data.status not in ('planned', 'cancelled'):
        raise HTTPException(status_code=400, detail="Status must be planned or cancelled")
    if plan['trade_group']:
        raise HTTPException(status_code=400, detail="Detach the plan from its trade first")
    conn.execute("UPDATE trade_plans SET status=?, updated_at=datetime('now') WHERE id=?", (data.status, plan_id))
    conn.commit()
    return _get_plan(conn, plan_id)


@router.delete("/plans/{plan_id:int}")
def delete_plan(plan_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    if _get_plan(conn, plan_id)['trade_group']:
        raise HTTPException(status_code=400, detail="Detach the plan from its trade first")
    conn.execute("DELETE FROM trade_plans WHERE id=?", (plan_id,))
    conn.commit()
    return {"deleted": True, "id": plan_id}


@router.get("/trades/{trade_id:int}/plan")
def get_trade_plan(trade_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    trade = _get_trade(conn, trade_id)
    row = conn.execute("SELECT * FROM trade_plans WHERE account_id=? AND trade_group=?",
                       (trade['account_id'], trade['trade_group'])).fetchone()
    if row:
        return {"plan": _with_trade(conn, row_to_dict(row)), "candidates": []}
    return {"plan": None, "candidates": _open_plans(conn, trade['account_id'], trade['ticker'], trade['side'])}


@router.post("/trades/{trade_id:int}/plan")
def attach_plan(trade_id: int, data: AttachBody, conn: sqlite3.Connection = Depends(get_connection)):
    trade = _get_trade(conn, trade_id)
    plan = _get_plan(conn, data.plan_id)
    if plan['status'] != 'planned':
        raise HTTPException(status_code=400, detail="This plan is already used or cancelled")
    if (plan['account_id'], plan['symbol'], plan['direction']) != (trade['account_id'], (trade['ticker'] or '').upper(), trade['side']):
        raise HTTPException(status_code=400, detail="This plan is for a different account, symbol or direction")
    if conn.execute("SELECT 1 FROM trade_plans WHERE account_id=? AND trade_group=?",
                    (trade['account_id'], trade['trade_group'])).fetchone():
        raise HTTPException(status_code=400, detail="This trade already has a plan")
    opening = _opening_fill(trade['executions'])
    conn.execute(
        "UPDATE trade_plans SET status='active', trade_group=?, opening_fill_key=?, updated_at=datetime('now') WHERE id=?",
        (trade['trade_group'], fill_key(opening) if opening else None, plan['id']),
    )
    _fill_analysis(conn, trade, plan)
    conn.commit()
    return _get_plan(conn, plan['id'])


@router.delete("/trades/{trade_id:int}/plan")
def detach_plan(trade_id: int, conn: sqlite3.Connection = Depends(get_connection)):
    trade = _get_trade(conn, trade_id)
    row = conn.execute("SELECT * FROM trade_plans WHERE account_id=? AND trade_group=?",
                       (trade['account_id'], trade['trade_group'])).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="This trade has no plan")
    _change_analysis(conn, trade['trade_group'], row_to_dict(row), None)
    release_plan(conn, trade['account_id'], trade['trade_group'])
    conn.commit()
    return {"detached": True, "id": row['id']}

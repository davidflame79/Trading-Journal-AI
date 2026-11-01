"""Brain chat and Day Review read the plans, comments and summaries written on trades."""
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HEADER = "date,time,symbol,side,quantity,price\n"
BUY = "2026-10-19,10:00:00,NU,BUY,2,14\n"
OTHER = "2026-10-19,11:00:00,TSLA,BUY,2,300\n"
PLAN = {"account_id": 1, "symbol": "NU", "direction": "LONG", "setup_type": "Breakout",
        "invalidation": "Closes below the SMA5", "stop_price": 13.5}


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main", "library", "trade_comments", "trade_plans", "trade_review", "trade_summary",
                 "trade_notes", "ai_analysis", "daily_summary"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    monkeypatch.setattr(main, "_classify_dates", lambda conn, dates: 0, raising=False)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        conn = sqlite3.connect(db)
        conn.execute("INSERT OR IGNORE INTO accounts (id, name, type) VALUES (1, 'Swing', 'swing_trading')")
        conn.execute("INSERT OR IGNORE INTO accounts (id, name, type) VALUES (2, 'Other', 'swing_trading')")
        conn.commit()
        conn.close()
        c.db = str(db)
        yield c


def post(client, rows, account="1"):
    r = client.post("/api/import-csv", data={"account_id": account, "broker": "generic"},
                    files={"file": ("fills.csv", (HEADER + rows).encode(), "text/csv")})
    assert r.status_code == 200, r.text


def connect(client):
    conn = sqlite3.connect(client.db)
    conn.row_factory = sqlite3.Row
    return conn


def trade(client, ticker="NU", account=1):
    return dict(connect(client).execute("SELECT * FROM trades WHERE ticker=? AND account_id=?", (ticker, account)).fetchone())


def annotate(client):
    post(client, BUY + OTHER)
    t = trade(client)
    plan = client.post("/api/plans", json=PLAN).json()
    client.post(f"/api/trades/{t['id']}/plan", json={"plan_id": plan["id"]})
    client.post(f"/api/trades/{t['id']}/comments", json={"body": "Added at support", "note_date": "2026-10-20"})


def test_brain_reads_the_plan_and_comments_under_their_trade(client):
    from ai_analysis import build_brain_context
    annotate(client)
    text = build_brain_context(connect(client), 1)
    nu = text.index("| NU |")
    tsla = text.index("| TSLA |")
    block = text[nu:tsla] if nu < tsla else text[nu:]
    assert "Closes below the SMA5" in block
    assert "Comment 2026-10-20: Added at support" in block


def test_brain_sees_a_plan_not_yet_taken(client):
    from ai_analysis import build_brain_context
    post(client, OTHER)
    client.post("/api/plans", json=PLAN)
    assert "NU LONG" in build_brain_context(connect(client), 1)


def test_brain_reads_diary_summaries(client):
    from ai_analysis import build_brain_context
    post(client, BUY)
    t = trade(client)
    conn = connect(client)
    conn.execute("INSERT INTO trade_summaries (account_id, trade_group, close_date, status, content) VALUES (1,?,?,?,?)",
                 (t["trade_group"], "2026-10-23", "ready",
                  json.dumps({"headline": "Clean breakout", "lessons": ["Keep sizing"]})))
    conn.commit()
    text = build_brain_context(conn, 1)
    assert "Clean breakout" in text and "Keep sizing" in text


def test_brain_keeps_other_accounts_out(client):
    from ai_analysis import build_brain_context
    annotate(client)
    post(client, "2026-10-19,12:00:00,AMD,BUY,1,100\n", account="2")
    text = build_brain_context(connect(client), 2)
    assert "Added at support" not in text and "Closes below" not in text


def test_a_long_comment_is_cut_short(client):
    from trade_notes import MAX_NOTE_CHARS, trade_note_lines
    post(client, BUY)
    t = trade(client)
    client.post(f"/api/trades/{t['id']}/comments", json={"body": "x" * 1000, "note_date": "2026-10-20"})
    line = trade_note_lines(connect(client), 1, t["trade_group"])[0]
    assert len(line) < MAX_NOTE_CHARS + 30


def test_day_review_prompt_includes_the_plan_and_comments(client, monkeypatch):
    import daily_summary
    annotate(client)
    sent = []

    def create(**kwargs):
        sent.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="{}")])
    monkeypatch.setattr(daily_summary, "get_client", lambda: SimpleNamespace(messages=SimpleNamespace(create=create)))

    daily_summary.generate_daily_summary(daily_summary.build_daily_context(connect(client), "2026-10-19", 1))

    assert "Closes below the SMA5" in sent[0]
    assert "Comment 2026-10-20: Added at support" in sent[0]

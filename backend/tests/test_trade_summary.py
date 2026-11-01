"""The Diary summary written when an import closes a trade.

    cd backend && python -m pytest tests -q
"""
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HEADER = "date,time,symbol,side,quantity,price\n"
SUMMARY = {"headline": "Clean breakout", "story": "You bought NU.", "against_plan": "On plan.", "lessons": ["Keep sizing"]}


def days_ago(n):
    return (datetime.utcnow().date() - timedelta(days=n)).isoformat()


def buy(symbol="NU", ago=5, qty=2):
    return f"{days_ago(ago)},10:00:00,{symbol},BUY,{qty},14\n"


def sell(symbol="NU", ago=2, qty=2):
    return f"{days_ago(ago)},15:00:00,{symbol},SELL,{qty},16\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main", "library", "trade_comments", "trade_plans", "trade_review", "trade_summary"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    monkeypatch.setattr(main, "_classify_dates", lambda conn, dates: 0, raising=False)

    async def fake_chart(ticker, day, timeframe, days_back):
        return {"bars": []}
    monkeypatch.setattr(main, "get_chart", fake_chart)

    def fake_summary(facts, bars, today):
        fake_summary.calls.append(facts)
        return dict(SUMMARY)
    fake_summary.calls = []
    monkeypatch.setattr(main, "generate_summary", fake_summary)

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        conn = sqlite3.connect(db)
        conn.execute("INSERT OR IGNORE INTO accounts (id, name, type) VALUES (1, 'Swing', 'swing_trading')")
        conn.commit()
        conn.close()
        c.db = str(db)
        c.ai_calls = fake_summary.calls
        c.main = main
        yield c


def post(client, rows):
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("fills.csv", (HEADER + rows).encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def summaries(client):
    return client.get("/api/trade-summaries").json()


def trades(client):
    conn = sqlite3.connect(client.db)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY date, id")]


def test_opening_a_trade_writes_nothing(client):
    assert post(client, buy())["summaries_writing"] == 0
    assert summaries(client) == []
    assert client.ai_calls == []


def test_a_partial_exit_writes_nothing(client):
    post(client, buy() + sell(qty=1))
    assert summaries(client) == []


def test_the_import_that_closes_a_trade_writes_a_summary_dated_on_the_close(client):
    post(client, buy())
    assert post(client, sell())["summaries_writing"] == 1

    s = summaries(client)[0]
    assert (s["status"], s["close_date"], s["content"]) == ("ready", days_ago(2), SUMMARY)
    assert s["trade"]["ticker"] == "NU"
    assert s["out_of_date"] is False


def test_importing_the_same_file_again_does_not_write_again(client):
    rows = buy() + sell()
    post(client, rows)
    assert post(client, rows)["summaries_writing"] == 0
    assert len(client.ai_calls) == 1


def test_a_deleted_summary_is_not_written_again_by_a_reimport(client):
    rows = buy() + sell()
    post(client, rows)
    client.delete(f"/api/trade-summaries/{summaries(client)[0]['id']}")
    post(client, rows)
    assert summaries(client) == []
    assert len(client.ai_calls) == 1


def test_an_old_close_in_a_history_import_costs_nothing(client):
    assert post(client, buy(ago=400) + sell(ago=390))["summaries_writing"] == 0
    assert summaries(client) == []
    assert client.ai_calls == []


def test_only_a_few_are_written_per_import_and_the_rest_wait_for_a_click(client):
    rows = "".join(buy(f"T{i}", ago=20 - i) + sell(f"T{i}", ago=10 - i) for i in range(7))
    assert post(client, rows)["summaries_writing"] == 5
    assert len(client.ai_calls) == 5
    statuses = sorted(s["status"] for s in summaries(client))
    assert statuses == ["pending", "pending"] + ["ready"] * 5
    waiting = [s for s in summaries(client) if s["status"] == "pending"]
    assert {s["trade"]["ticker"] for s in waiting} == {"T0", "T1"}


def test_a_failure_does_not_fail_the_import_and_can_be_tried_again(client, monkeypatch):
    def no_key(facts, bars, today):
        raise ValueError("ANTHROPIC_API_KEY is not set in .env file")
    good = client.main.generate_summary
    monkeypatch.setattr(client.main, "generate_summary", no_key)
    post(client, buy() + sell())
    s = summaries(client)[0]
    assert s["status"] == "failed"
    assert "ANTHROPIC_API_KEY" in s["error"]

    monkeypatch.setattr(client.main, "generate_summary", good)
    r = client.post(f"/api/trades/{s['trade']['id']}/summary")
    assert r.status_code == 200
    assert r.json()["summary"]["status"] == "ready"
    assert r.json()["summary"]["error"] is None


def test_a_failed_rewrite_keeps_the_earlier_summary(client, monkeypatch):
    post(client, buy() + sell())
    t = trades(client)[0]

    def broken(facts, bars, today):
        raise ValueError("cut off")
    monkeypatch.setattr(client.main, "generate_summary", broken)
    r = client.post(f"/api/trades/{t['id']}/summary").json()["summary"]
    assert (r["status"], r["error"], r["content"]) == ("failed", "cut off", SUMMARY)


def test_a_summary_can_be_written_by_hand_for_a_trade_closed_earlier(client):
    post(client, buy(ago=400) + sell(ago=390))
    t = trades(client)[0]
    assert client.get(f"/api/trades/{t['id']}/summary").json() == {"summary": None}
    assert client.post(f"/api/trades/{t['id']}/summary").json()["summary"]["status"] == "ready"


def test_an_open_trade_cannot_have_a_summary(client):
    post(client, buy())
    assert client.post(f"/api/trades/{trades(client)[0]['id']}/summary").status_code == 400


def test_a_summary_goes_out_of_date_when_the_trade_changes(client):
    post(client, buy() + sell())
    t = trades(client)[0]
    client.post(f"/api/trades/{t['id']}/comments", json={"body": "Sold into strength", "note_date": days_ago(2)})
    assert summaries(client)[0]["out_of_date"] is True
    assert len(client.ai_calls) == 1


def test_rebuying_after_closing_gets_its_own_summary(client):
    post(client, buy(ago=9) + sell(ago=8))
    post(client, buy(ago=5) + sell(ago=2))
    assert len(summaries(client)) == 2


def test_clearing_a_diary_day_leaves_summaries(client):
    post(client, buy() + sell())
    client.delete(f"/api/diary/by-date/{days_ago(2)}")
    assert len(summaries(client)) == 1


def test_deleting_the_trade_deletes_its_summary(client):
    post(client, buy() + sell())
    client.delete(f"/api/trades/{trades(client)[0]['id']}")
    assert summaries(client) == []


def test_a_write_interrupted_by_a_restart_can_be_tried_again(client):
    from trade_summary import init_trade_summaries_table
    post(client, buy() + sell())
    conn = sqlite3.connect(client.db)
    conn.row_factory = sqlite3.Row
    conn.execute("UPDATE trade_summaries SET status='writing'")
    conn.commit()
    init_trade_summaries_table(conn)
    conn.close()
    assert summaries(client)[0]["status"] == "failed"


def test_the_list_filters_by_account(client):
    post(client, buy() + sell())
    assert client.get("/api/trade-summaries", params={"account_id": 2}).json() == []
    assert len(client.get("/api/trade-summaries", params={"account_id": 1}).json()) == 1

"""AI feedback on one trade: when it runs, what it is given, and how it stays with the trade.

    cd backend && python -m pytest tests -q
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HEADER = "date,time,symbol,side,quantity,price\n"
BUY = "2026-10-19,10:00:00,NU,BUY,2,14\n"
ADD = "2026-10-20,10:00:00,NU,BUY,2,16\n"
SELL = "2026-10-23,15:20:00,NU,SELL,4,18\n"
REBUY = "2026-10-23,15:40:00,NU,BUY,5,13\n"
PLAN = {"account_id": 1, "symbol": "NU", "direction": "LONG", "setup_type": "Breakout", "stop_price": 13.5}
BARS = [{"t": "2026-10-19T04:00:00Z", "o": 14, "h": 15, "l": 13, "c": 14.5, "v": 100}]
FEEDBACK = {"grade": "B", "summary": "Followed the plan."}


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main", "library", "trade_comments", "trade_plans", "trade_review"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    monkeypatch.setattr(main, "_classify_dates", lambda conn, dates: 0, raising=False)

    async def fake_chart(ticker, day, timeframe, days_back):
        fake_chart.calls.append((ticker, day, timeframe, days_back))
        return {"bars": BARS}
    fake_chart.calls = []
    monkeypatch.setattr(main, "get_chart", fake_chart)

    def fake_review(facts, bars, today):
        fake_review.calls.append((facts, bars))
        return dict(FEEDBACK)
    fake_review.calls = []
    monkeypatch.setattr(main, "generate_review", fake_review)

    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        conn = sqlite3.connect(db)
        conn.execute("INSERT OR IGNORE INTO accounts (id, name, type) VALUES (1, 'Swing', 'swing_trading')")
        conn.commit()
        conn.close()
        c.db = str(db)
        c.ai_calls = fake_review.calls
        c.chart_calls = fake_chart.calls
        c.main = main
        yield c


def post(client, rows):
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("fills.csv", (HEADER + rows).encode(), "text/csv")})
    assert r.status_code == 200, r.text


def trades(client):
    conn = sqlite3.connect(client.db)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY date, id")]


def run(client, trade):
    return client.post(f"/api/trades/{trade['id']}/review")


def saved(client, trade):
    return client.get(f"/api/trades/{trade['id']}/review").json()


def test_opening_a_trade_never_calls_the_ai(client):
    post(client, BUY)
    assert saved(client, trades(client)[0]) == {"review": None}
    assert client.ai_calls == []


def test_feedback_is_given_the_plan_fills_comments_and_bars(client):
    post(client, BUY + ADD)
    t = trades(client)[0]
    plan = client.post("/api/plans", json=PLAN).json()
    client.post(f"/api/trades/{t['id']}/plan", json={"plan_id": plan["id"]})
    client.post(f"/api/trades/{t['id']}/comments", json={"body": "Added at support", "note_date": "2026-10-20"})

    r = run(client, t)

    assert r.status_code == 200, r.text
    assert r.json()["review"] == FEEDBACK
    facts, bars = client.ai_calls[0]
    assert facts["plan"]["setup_type"] == "Breakout"
    assert facts["status"] == "open"
    assert [c["text"] for c in facts["comments"]] == ["Added at support"]
    assert [(f["shares_held_after"], f["avg_cost_after"]) for f in facts["fills"]] == [(2, 14), (4, 15)]
    assert bars == BARS
    assert client.chart_calls[0][2] == "1Day"


def test_a_comment_on_a_fill_says_which_fill(client):
    post(client, BUY)
    t = trades(client)[0]
    key = "2026-10-19|10:00:00|BOT|2.000000|14.000000"
    client.post(f"/api/trades/{t['id']}/comments", json={"body": "Starter", "fill_key": key})
    run(client, t)
    assert client.ai_calls[0][0]["comments"][0]["on_fill"] == "BOT 2 @ 14"


def test_a_closed_trade_gets_bars_up_to_its_exit_and_an_open_one_up_to_today(client):
    from trade_review import bars_window
    today = date(2026, 11, 1)
    closed = {"status": "closed", "fills": [{"date": "2026-10-19"}, {"date": "2026-10-23"}]}
    assert bars_window(closed, today) == ("2026-10-23", 65)
    assert bars_window({**closed, "status": "open"}, today) == ("2026-11-01", 74)


def test_missing_prices_do_not_stop_the_feedback(client, monkeypatch):
    async def no_chart(ticker, day, timeframe, days_back):
        return {"bars": [], "warning": "no keys"}
    monkeypatch.setattr(client.main, "get_chart", no_chart)
    post(client, BUY)
    assert run(client, trades(client)[0]).status_code == 200
    assert client.ai_calls[0][1] == []


def test_a_short_builds_its_position_on_the_sells(client):
    post(client, "2026-10-19,10:00:00,NU,SELL,4,50\n2026-10-20,10:00:00,NU,BUY,4,40\n")
    run(client, trades(client)[0])
    assert [(f["shares_held_after"], f["avg_cost_after"]) for f in client.ai_calls[0][0]["fills"]] == [(4, 50), (0, 50)]


def test_no_api_key_says_what_to_do_and_keeps_earlier_feedback(client, monkeypatch):
    post(client, BUY)
    t = trades(client)[0]
    run(client, t)

    def no_key(facts, bars, today):
        raise ValueError("ANTHROPIC_API_KEY is not set in .env file")
    monkeypatch.setattr(client.main, "generate_review", no_key)
    r = run(client, t)

    assert r.status_code == 400
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]
    assert saved(client, t)["review"] == FEEDBACK


def test_a_failed_run_reports_an_error_and_saves_nothing(client, monkeypatch):
    post(client, BUY)
    t = trades(client)[0]

    def broken(facts, bars, today):
        raise json.JSONDecodeError("bad", "", 0)
    monkeypatch.setattr(client.main, "generate_review", broken)

    assert run(client, t).status_code == 502
    assert saved(client, t) == {"review": None}


def test_feedback_goes_out_of_date_when_the_trade_changes(client):
    post(client, BUY)
    t = trades(client)[0]
    run(client, t)
    assert saved(client, t)["out_of_date"] is False

    client.post(f"/api/trades/{t['id']}/comments", json={"body": "Moved stop", "note_date": "2026-10-20"})
    assert saved(client, t)["out_of_date"] is True

    run(client, t)
    assert saved(client, t)["out_of_date"] is False


def test_changing_the_stop_puts_feedback_out_of_date(client):
    post(client, BUY)
    t = trades(client)[0]
    run(client, t)
    client.patch(f"/api/trades/{t['trade_group']}/analysis", json={"stop_loss": 13.0})
    assert saved(client, t)["out_of_date"] is True


def test_feedback_follows_the_trade_when_new_fills_rename_it(client):
    post(client, BUY)
    t = trades(client)[0]
    run(client, t)

    post(client, SELL.replace(",4,", ",2,"))

    renamed = trades(client)[0]
    assert renamed["trade_group"] != t["trade_group"]
    review = saved(client, renamed)
    assert review["review"] == FEEDBACK
    assert review["out_of_date"] is True


def test_feedback_stays_with_the_opening_fill_when_a_late_import_splits_the_trade(client):
    post(client, BUY + REBUY)
    run(client, trades(client)[0])

    post(client, "2026-10-20,10:00:00,NU,SELL,2,15\n")

    first, second = trades(client)
    assert saved(client, first)["review"] == FEEDBACK
    assert saved(client, second) == {"review": None}


def test_rebuying_after_going_flat_starts_without_feedback(client):
    post(client, BUY + ADD + SELL)
    run(client, trades(client)[0])
    post(client, REBUY)
    first, second = trades(client)
    assert saved(client, first)["review"] == FEEDBACK
    assert saved(client, second) == {"review": None}


def test_deleting_the_trade_deletes_its_feedback(client):
    post(client, BUY)
    t = trades(client)[0]
    run(client, t)
    client.delete(f"/api/trades/{t['id']}")
    post(client, BUY)
    assert saved(client, trades(client)[0]) == {"review": None}


def test_unknown_trade_is_not_found(client):
    assert client.get("/api/trades/99/review").status_code == 404
    assert client.post("/api/trades/99/review").status_code == 404

"""Comments on a trade's fills: they must outlive imports that rename or split the trade.

    cd backend && python -m pytest tests -q
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

HEADER = "date,time,symbol,side,quantity,price\n"
BUY = "2026-10-19,10:00:00,TSLA,BUY,2,100\n"
SELL_1 = "2026-10-20,10:00:00,TSLA,SELL,1,110\n"
SELL_2 = "2026-10-23,15:20:00,TSLA,SELL,1,120\n"
REBUY = "2026-10-23,15:40:00,TSLA,BUY,5,90\n"
RESELL = "2026-10-26,10:00:00,TSLA,SELL,5,95\n"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main", "library", "trade_comments"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    monkeypatch.setattr(main, "_classify_dates", lambda conn, dates: 0, raising=False)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        conn = sqlite3.connect(db)
        conn.execute("INSERT OR IGNORE INTO accounts (id, name, type) VALUES (1, 'Swing', 'swing_trading')")
        conn.commit()
        conn.close()
        c.db = str(db)
        yield c


def post(client, rows):
    r = client.post("/api/import-csv", data={"account_id": "1", "broker": "generic"},
                    files={"file": ("fills.csv", (HEADER + rows).encode(), "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def trades(client):
    conn = sqlite3.connect(client.db)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY date, id")]


def key_of(trade, action, date):
    from trade_comments import fill_key
    return next(fill_key(e) for e in json.loads(trade["executions"])
                if e["action"] == action and e["date"] == date)


def comment_on_fill(client, trade, action, date, body):
    r = client.post(f"/api/trades/{trade['id']}/comments", json={"body": body, "fill_key": key_of(trade, action, date)})
    assert r.status_code == 201, r.text
    return r.json()


def comments_of(client, trade):
    return [c["body"] for c in client.get(f"/api/trades/{trade['id']}/comments").json()]


def test_comment_takes_its_date_from_the_fill(client):
    post(client, BUY)
    c = comment_on_fill(client, trades(client)[0], "BOT", "2026-10-19", "Starter at support")
    assert c["note_date"] == "2026-10-19"


def test_note_needs_text_a_date_and_a_fill_of_this_trade(client):
    post(client, BUY)
    tid = trades(client)[0]["id"]
    assert client.post(f"/api/trades/{tid}/comments", json={"body": "  ", "note_date": "2026-10-20"}).status_code == 400
    assert client.post(f"/api/trades/{tid}/comments", json={"body": "x"}).status_code == 400
    assert client.post(f"/api/trades/{tid}/comments", json={"body": "x", "fill_key": "nope"}).status_code == 400
    assert client.post("/api/trades/9999/comments", json={"body": "x", "note_date": "2026-10-20"}).status_code == 404


def test_edit_and_delete(client):
    post(client, BUY)
    t = trades(client)[0]
    c = client.post(f"/api/trades/{t['id']}/comments", json={"body": "Moved stop", "note_date": "2026-10-20"}).json()
    assert client.patch(f"/api/trade-comments/{c['id']}", json={"body": "Moved stop to 95"}).json()["body"] == "Moved stop to 95"
    assert comments_of(client, t) == ["Moved stop to 95"]
    assert client.delete(f"/api/trade-comments/{c['id']}").status_code == 200
    assert comments_of(client, t) == []


def test_comments_survive_new_fills_being_imported_into_an_open_trade(client):
    post(client, BUY)
    t = trades(client)[0]
    comment_on_fill(client, t, "BOT", "2026-10-19", "Starter at support")
    client.post(f"/api/trades/{t['id']}/comments", json={"body": "Earnings next week", "note_date": "2026-10-19"})

    post(client, SELL_1)
    post(client, SELL_2)

    stored = trades(client)
    assert len(stored) == 1
    assert stored[0]["trade_group"] != t["trade_group"]  # the name follows the closing date
    assert comments_of(client, stored[0]) == ["Starter at support", "Earnings next week"]


def test_rebuying_after_going_flat_is_a_new_trade_with_no_comments(client):
    post(client, BUY + SELL_1 + SELL_2)
    comment_on_fill(client, trades(client)[0], "SOLD", "2026-10-23", "Exit at target")

    post(client, REBUY + RESELL)

    first, second = trades(client)
    assert comments_of(client, first) == ["Exit at target"]
    assert comments_of(client, second) == []


def test_comment_follows_its_fill_when_a_late_import_splits_the_trade(client):
    # bought 2 + 5 looks like one position until the missing sell of 2 arrives between them
    post(client, BUY + REBUY)
    t = trades(client)[0]
    comment_on_fill(client, t, "BOT", "2026-10-19", "First idea")
    comment_on_fill(client, t, "BOT", "2026-10-23", "Second idea")

    post(client, "2026-10-20,10:00:00,TSLA,SELL,2,110\n")

    first, second = trades(client)
    assert comments_of(client, first) == ["First idea"]
    assert comments_of(client, second) == ["Second idea"]


def test_comment_follows_a_fill_edited_by_hand_and_stays_when_it_is_deleted(client):
    post(client, BUY + SELL_1)
    t = trades(client)[0]
    comment_on_fill(client, t, "SOLD", "2026-10-20", "Trimmed")
    execs = json.loads(t["executions"])
    idx = next(i for i, e in enumerate(execs) if e["action"] == "SOLD")

    r = client.put(f"/api/trades/{t['id']}/executions/{idx}",
                   json={**execs[idx], "date": "2026-10-21", "price": 111})
    assert r.status_code == 200
    comment = client.get(f"/api/trades/{t['id']}/comments").json()[0]
    assert comment["note_date"] == "2026-10-21"
    assert comment["fill_key"] == key_of(trades(client)[0], "SOLD", "2026-10-21")

    assert client.delete(f"/api/trades/{t['id']}/executions/{idx}").status_code == 200
    comment = client.get(f"/api/trades/{t['id']}/comments").json()[0]
    assert (comment["fill_key"], comment["body"]) == (None, "Trimmed")


def test_deleting_a_trade_deletes_its_comments(client):
    post(client, BUY + SELL_1 + SELL_2)
    t = trades(client)[0]
    comment_on_fill(client, t, "BOT", "2026-10-19", "Gone with the trade")
    client.delete(f"/api/trades/{t['id']}")

    # the same fills come back as a fresh trade, with nothing left over
    post(client, BUY + SELL_1 + SELL_2)
    assert comments_of(client, trades(client)[0]) == []


def test_import_lists_only_new_fills_without_a_comment(client):
    first = post(client, BUY)
    assert [(t["ticker"], t["new_fill_count"]) for t in first["needs_comment"]] == [("TSLA", 1)]
    comment_on_fill(client, trades(client)[0], "BOT", "2026-10-19", "Starter")

    # the commented buy and any old fill are not asked about again, only the two new sells
    second = post(client, SELL_1 + SELL_2)
    assert [t["new_fill_count"] for t in second["needs_comment"]] == [2]

    # importing the same file again adds nothing, so there is nothing to ask
    assert post(client, SELL_1 + SELL_2)["needs_comment"] == []

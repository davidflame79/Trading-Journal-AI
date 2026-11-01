"""Plans written before a trade, and how they attach to it.

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
BUY = "2026-10-19,10:00:00,NU,BUY,2,14\n"
SELL_1 = "2026-10-20,10:00:00,NU,SELL,1,15\n"
SELL_2 = "2026-10-23,15:20:00,NU,SELL,1,16\n"
REBUY = "2026-10-23,15:40:00,NU,BUY,5,13\n"
SETUP = {
    "account_id": 1, "symbol": "nu", "direction": "LONG", "plan_date": "2026-10-18",
    "setup_type": "Trend Continuation", "idea_source": "NN Scanner", "entry_trigger": "Break of SMA5",
    "invalidation": "SMA5 fails to turn up", "entry_price": 14.0, "stop_price": 13.5,
    "target_price": 15.5, "shares": 2, "account_size": 25000,
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main", "library", "trade_comments", "trade_plans"):
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
    return r.json()


def new_plan(client, **changes):
    r = client.post("/api/plans", json={**SETUP, **changes})
    assert r.status_code == 201, r.text
    return r.json()


def trades(client):
    conn = sqlite3.connect(client.db)
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY date, id")]


def analysis(client, trade):
    return client.get(f"/api/trades/{trade['trade_group']}/analysis").json()["analysis"]


def attach(client, trade, plan):
    return client.post(f"/api/trades/{trade['id']}/plan", json={"plan_id": plan["id"]})


def plan_of(client, trade):
    return client.get(f"/api/trades/{trade['id']}/plan").json()["plan"]


def test_a_new_plan_is_planned_and_its_symbol_uppercase(client):
    plan = new_plan(client)
    assert (plan["symbol"], plan["status"], plan["trade_group"]) == ("NU", "planned", None)


def test_a_plan_needs_a_symbol_a_direction_and_a_real_account(client):
    assert client.post("/api/plans", json={**SETUP, "symbol": "  "}).status_code == 400
    assert client.post("/api/plans", json={**SETUP, "direction": "SIDEWAYS"}).status_code == 400
    assert client.post("/api/plans", json={**SETUP, "account_id": 99}).status_code == 400


def test_import_offers_only_plans_of_the_same_account_symbol_and_direction(client):
    plan = new_plan(client)
    new_plan(client, symbol="TSLA")
    new_plan(client, direction="SHORT")
    new_plan(client, account_id=2)
    matches = post(client, BUY)["plan_matches"]
    assert len(matches) == 1
    assert [p["id"] for p in matches[0]["plans"]] == [plan["id"]]


def test_import_offers_nothing_for_more_fills_on_a_trade_already_open(client):
    new_plan(client)
    post(client, BUY)
    assert post(client, SELL_1)["plan_matches"] == []


def test_attaching_fills_the_empty_analysis_fields_but_not_what_is_already_written(client):
    plan = new_plan(client)
    post(client, BUY)
    t = trades(client)[0]
    client.patch(f"/api/trades/{t['trade_group']}/analysis", json={"entry_reason": "My own words"})

    assert attach(client, t, plan).json()["status"] == "active"

    a = analysis(client, t)
    assert (a["strategy"], a["idea_source"], a["stop_loss"], a["target_price"]) == ("Trend Continuation", "NN Scanner", 13.5, 15.5)
    assert a["entry_reason"] == "My own words"


def test_a_plan_attaches_to_one_trade_and_a_trade_takes_one_plan(client):
    plan, other = new_plan(client), new_plan(client)
    post(client, BUY + SELL_1 + SELL_2)
    post(client, REBUY)
    first, second = trades(client)

    assert attach(client, first, plan).status_code == 200
    assert attach(client, second, plan).status_code == 400
    assert attach(client, first, other).status_code == 400


def test_a_plan_for_something_else_is_refused(client):
    plan = new_plan(client, symbol="TSLA")
    post(client, BUY)
    assert attach(client, trades(client)[0], plan).status_code == 400


def test_rebuying_after_going_flat_is_offered_a_new_plan_not_the_used_one(client):
    used, fresh = new_plan(client), new_plan(client, setup_type="Reversal")
    post(client, BUY + SELL_1 + SELL_2)
    attach(client, trades(client)[0], used)

    matches = post(client, REBUY)["plan_matches"]

    assert [p["id"] for p in matches[0]["plans"]] == [fresh["id"]]
    assert plan_of(client, trades(client)[0])["id"] == used["id"]


def test_plan_stays_attached_when_new_fills_rename_the_trade(client):
    plan = new_plan(client)
    post(client, BUY)
    t = trades(client)[0]
    attach(client, t, plan)

    post(client, SELL_1)
    post(client, SELL_2)

    renamed = trades(client)[0]
    assert renamed["trade_group"] != t["trade_group"]
    assert plan_of(client, renamed)["id"] == plan["id"]
    assert analysis(client, renamed)["stop_loss"] == 13.5


def test_plan_stays_with_its_opening_fill_when_a_late_import_splits_the_trade(client):
    plan = new_plan(client)
    post(client, BUY + REBUY)
    attach(client, trades(client)[0], plan)

    post(client, "2026-10-20,10:00:00,NU,SELL,2,15\n")

    first, second = trades(client)
    assert plan_of(client, first)["id"] == plan["id"]
    assert plan_of(client, second) is None


def test_plan_stays_attached_when_the_opening_fill_is_edited_by_hand(client):
    plan = new_plan(client)
    post(client, BUY + SELL_1)
    t = trades(client)[0]
    attach(client, t, plan)
    execs = json.loads(t["executions"])
    idx = next(i for i, e in enumerate(execs) if e["action"] == "BOT")
    client.put(f"/api/trades/{t['id']}/executions/{idx}", json={**execs[idx], "price": 14.25})

    post(client, SELL_2)

    assert plan_of(client, trades(client)[0])["id"] == plan["id"]


def test_deleting_the_trade_returns_the_plan_to_planned(client):
    plan = new_plan(client)
    post(client, BUY)
    t = trades(client)[0]
    attach(client, t, plan)

    client.delete(f"/api/trades/{t['id']}")

    assert [(p["status"], p["trade_group"]) for p in client.get("/api/plans").json()] == [("planned", None)]
    assert [p["id"] for p in post(client, BUY)["plan_matches"][0]["plans"]] == [plan["id"]]


def test_detach_clears_what_the_plan_filled_in_but_not_what_was_changed_by_hand(client):
    plan = new_plan(client)
    post(client, BUY)
    t = trades(client)[0]
    attach(client, t, plan)
    client.patch(f"/api/trades/{t['trade_group']}/analysis", json={"stop_loss": 13.0})

    assert client.delete(f"/api/trades/{t['id']}/plan").status_code == 200

    a = analysis(client, t)
    assert (a["target_price"], a["stop_loss"]) == (None, 13.0)
    assert client.get(f"/api/trades/{t['id']}/plan").json()["candidates"][0]["id"] == plan["id"]


def test_editing_an_attached_plan_updates_the_trade_unless_it_was_changed_by_hand(client):
    plan = new_plan(client)
    post(client, BUY)
    t = trades(client)[0]
    attach(client, t, plan)
    client.patch(f"/api/trades/{t['trade_group']}/analysis", json={"stop_loss": 13.0})

    r = client.put(f"/api/plans/{plan['id']}", json={**SETUP, "stop_price": 13.25, "target_price": 16.0})

    assert r.status_code == 200
    a = analysis(client, t)
    assert (a["stop_loss"], a["target_price"]) == (13.0, 16.0)


def test_an_attached_plan_cannot_change_symbol_or_be_cancelled_or_deleted(client):
    plan = new_plan(client)
    post(client, BUY)
    attach(client, trades(client)[0], plan)
    assert client.put(f"/api/plans/{plan['id']}", json={**SETUP, "symbol": "TSLA"}).status_code == 400
    assert client.patch(f"/api/plans/{plan['id']}/status", json={"status": "cancelled"}).status_code == 400
    assert client.delete(f"/api/plans/{plan['id']}").status_code == 400


def test_a_cancelled_plan_is_not_offered_and_can_be_restored(client):
    plan = new_plan(client)
    client.patch(f"/api/plans/{plan['id']}/status", json={"status": "cancelled"})
    assert post(client, BUY)["plan_matches"] == []
    t = trades(client)[0]
    assert attach(client, t, plan).status_code == 400

    client.patch(f"/api/plans/{plan['id']}/status", json={"status": "planned"})
    assert attach(client, t, plan).status_code == 200


def test_dropdowns_start_with_the_setup_sheet_lists(client):
    options = client.get("/api/plans/options").json()
    assert options["idea_sources"][:2] == ["Scanner", "Didi"]
    assert "Trend Continuation" in options["setup_types"]
    assert options["higher_tf_trends"] == ["Uptrend", "Downtrend", "Range / Choppy"]


def test_dropdown_lists_are_tidied_and_keep_their_order(client):
    r = client.put("/api/plans/options", json={
        "idea_sources": [" NN Scanner ", "", "Didi", "didi"], "setup_types": [], "higher_tf_trends": ["Up"],
    })
    assert r.status_code == 200
    saved = client.get("/api/plans/options").json()
    assert saved == {"idea_sources": ["NN Scanner", "Didi"], "setup_types": [], "higher_tf_trends": ["Up"]}


def test_changing_a_dropdown_list_leaves_written_plans_alone(client):
    plan = new_plan(client)
    client.put("/api/plans/options", json={"idea_sources": [], "setup_types": [], "higher_tf_trends": []})
    assert client.get("/api/plans").json()[0]["setup_type"] == plan["setup_type"]


def test_a_plan_can_be_written_as_cancelled_but_not_as_filled(client):
    assert new_plan(client, status="cancelled")["status"] == "cancelled"
    assert client.post("/api/plans", json={**SETUP, "status": "active"}).status_code == 400


def test_saving_a_plan_without_a_status_keeps_it(client):
    plan = new_plan(client, status="cancelled")
    assert client.put(f"/api/plans/{plan['id']}", json=SETUP).json()["status"] == "cancelled"
    assert client.put(f"/api/plans/{plan['id']}", json={**SETUP, "status": "planned"}).json()["status"] == "planned"


def test_an_attached_plan_cannot_be_given_a_status_by_hand(client):
    plan = new_plan(client)
    post(client, BUY)
    attach(client, trades(client)[0], plan)
    assert client.put(f"/api/plans/{plan['id']}", json={**SETUP, "status": "cancelled"}).status_code == 400


def test_plan_list_links_the_trade_and_says_when_it_closed(client):
    plan = new_plan(client)
    post(client, BUY)
    attach(client, trades(client)[0], plan)
    listed = client.get("/api/plans", params={"account_id": 1}).json()[0]
    assert (listed["trade"]["ticker"], listed["closed"]) == ("NU", False)

    post(client, SELL_1 + SELL_2)
    assert client.get("/api/plans").json()[0]["closed"] is True
    assert client.get("/api/plans", params={"account_id": 2}).json() == []

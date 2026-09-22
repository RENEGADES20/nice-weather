import json
from types import ModuleType

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nice_weather.trading.backtest_view import (
    ReplayView,
    account_events,
    decision_events,
    request_parameters,
    router,
    run_detail,
)
from nice_weather.trading.storage import Requests, Results, connect


def test_account_signal_projection_is_scoped_and_native_fills_are_not_duplicated(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    account = "sandbox-kalshi-knyc"
    results.create(account, account, "sandbox", {"venue": "kalshi"})
    event = {"event_id": "trigger", "venue": "kalshi", "day": "2026-09-21",
             "strategy": "S1", "stage": "weather_trigger", "asof": 99,
             "legs": [{"token": "a", "quantity": 1}, {"token": "b", "quantity": 2}]}
    results.append(account, "signals", {"kind": "signal-events", "events": [event]})
    results.checkpoint(account, 1, snapshot(), "running")
    first = account_events(tmp_path, account, "kalshi", "2026-09-21")
    assert [(e["token"], e["quantity"]) for e in first["events"] if e["stage"] == "trigger"] == [
        ("a", 1), ("b", 2)]
    assert len([e for e in first["events"] if e["stage"] == "fill"]) == 2
    assert not account_events(tmp_path, account, "poly_us", "2026-09-21")["events"]
    assert not account_events(tmp_path, account, "kalshi", "2026-09-22")["events"]
    second = account_events(tmp_path, account, "kalshi", "2026-09-21", first["next"])
    assert all(e["stage"] == "fill" for e in second["events"])
    assert {e["id"] for e in second["events"]} <= {e["id"] for e in first["events"]}


def snapshot():
    fill = {"order_id": "o1", "token": "a", "ts": 100_000_000_000,
            "quantity": 1, "price": 0.4, "fee": 0.01, "side": "BUY"}
    return {"ts": 100_000_000_000, "venue": "kalshi", "cash": 99.18,
            "market_value": 0.8, "equity": 99.98, "total_pnl": -0.02, "fees": 0.02,
            "drawdown": 0.0002,
            "markets": [{"token": "a", "date": "2026-09-21"}],
            "positions": [{"token": "a"}], "settled": {},
            "orders": [{"order_id": "o1", "token": "a", "status": "FILLED", "filled": 2,
                        "owner": "S1", "quantity": 2, "price": 0.4}],
            "fills": [fill.copy(), fill.copy()]}


def test_native_fills_same_timestamp_and_legacy_history(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", "backtest-kalshi", "backtest", {"venue": "kalshi"})
    view = ReplayView()
    value = snapshot()
    view.observe(value)
    view.observe(value)
    assert len([e for e in view.events if e["stage"] == "fill"]) == 2
    view.flush(results, "run", 1)
    results.checkpoint("run", 1, value)
    detail = run_detail(tmp_path, "run")
    assert len([e for e in detail["events"] if e["stage"] == "fill"]) == 2
    assert detail["curve"][-1]["equity"] == value["equity"]
    assert detail["settlement"] == "pending"
    assert detail["history_complete"]
    results.create("legacy", "backtest-poly_us", "backtest", {"venue": "poly_us"})
    results.checkpoint("legacy", 0, value)
    legacy = run_detail(tmp_path, "legacy")
    assert len(legacy["curve"]) == 1
    assert not legacy["history_complete"]
    assert len(legacy["events"]) == 2


def test_task3_equity_preferred_and_pending_request_visible(tmp_path):
    requests = Requests(tmp_path / "requests" / "requests.sqlite3")
    requests.submit("queued", "backtest-kalshi", "backtest", "backtest",
                    {"start": 1, "end": 2, "cash": 50})
    app = FastAPI()
    app.include_router(router(tmp_path))
    client = TestClient(app)
    assert client.get("/api/backtests/queued").json()["status"] == "queued"
    assert client.get("/api/backtests/missing").status_code == 404
    results = Results(tmp_path / "results.sqlite3")
    results.create("queued", "backtest-kalshi", "backtest", {"venue": "kalshi"})
    with connect(results.path) as con:
        con.execute("CREATE TABLE simulation_equity (run_id TEXT, ts INTEGER, body TEXT)")
        con.execute("INSERT INTO simulation_equity VALUES (?,?,?)",
                    ("queued", 10, json.dumps({"ts": 10, "equity": None, "cash": 50})))
    detail = run_detail(tmp_path, "queued")
    assert detail["curve"] == [{"ts": 10, "time": 1e-8, "equity": None, "cash": 50}]
    assert len(client.get("/api/backtests").json()) == 1


def test_signal_stages_and_s1_legs_do_not_duplicate_native_fills():
    event = {"event_id": "s1", "basket_id": "basket", "asof": 100,
             "venue": "kalshi", "day": "2026-09-21", "strategy": "S1",
             "stage": "weather_trigger", "legs": [
                 {"token": "a", "quantity": 1}, {"token": "b", "quantity": 2}]}
    rows = decision_events({"feed_seq": 1, "native_index": 0, "received_at": 101,
                            "signal": {"strategy": "S1", "events": [
                                event, event | {"stage": "fill"}]}})
    assert [r["token"] for r in rows] == ["a", "b"]
    assert {r["time"] for r in rows} == {100}
    assert {r["stage"] for r in rows} == {"trigger"}
    assert {r["group_id"] for r in rows} == {"basket"}
    warning = event | {"stage": "weather_warning", "legs": [], "targets": [],
                       "warning": {"upper_bin": "c", "probability": 0.6}}
    rows = decision_events({"feed_seq": 1, "native_index": 0, "received_at": 101,
                            "signal": {"strategy": "S2", "events": [warning]}})
    assert rows[0]["token"] == "c"
    assert rows[0]["probability"] == 0.6


def test_date_and_parameter_handoff_uses_contract_window(tmp_path, monkeypatch):
    import sys

    market = ModuleType("nice_weather.trading.market_weather")
    market.market_day = lambda path, venue, day: {
        "observation_start": f"{day}T05:00:00+00:00",
        "observation_end": f"{day}T23:59:00+00:00"}
    execution = ModuleType("nice_weather.trading.paper_execution")
    execution.settings = lambda values: values
    monkeypatch.setitem(sys.modules, market.__name__, market)
    monkeypatch.setitem(sys.modules, execution.__name__, execution)
    result = request_parameters(tmp_path, "kalshi", {
        "start_day": "2026-09-19", "end_day": "2026-09-20", "cash": 250,
        "simulation": {"estimated_fee_rate": 0.02, "slippage_pp": 0.5}})
    assert result["start"] == 1789794000
    assert result["cash"] == 250
    assert result["simulation"]["slippage_pp"] == 0.5
    assert result["window_basis"] == "contract_observation_window"
    for bad in (True, 0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="cash"):
            request_parameters(tmp_path, "kalshi", {"cash": bad})

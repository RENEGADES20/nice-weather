from __future__ import annotations

import copy
import time
from datetime import UTC, datetime

import pytest

from nice_weather.trading.feed import FeedStore
from nice_weather.trading.signals import evaluate
from nice_weather.trading.us_markets import normalize_book


def test_capture_pauses_without_deleting_data_when_disk_is_low(tmp_path, monkeypatch):
    from types import SimpleNamespace

    store = FeedStore(tmp_path / "feed.sqlite3")
    store.capture("test", "https://example.com", 1, 2, b"original")
    store.publish("weather", "test", {"received_at": 2}, 2)
    before = store.snapshot()
    monkeypatch.setattr(
        "nice_weather.trading.feed.shutil.disk_usage", lambda _: SimpleNamespace(free=1024**3 - 1)
    )
    with pytest.raises(OSError, match="headroom"):
        store.capture("test", "https://example.com", 3, 4, b"new")
    with pytest.raises(OSError, match="headroom"):
        store.publish("weather", "test", {"received_at": 4}, 4)
    assert store.snapshot() == before
    # Health can report the failure while bulk writes remain paused.
    store.publish("health", "storage", {"status": "unavailable"}, 4)
    assert store.snapshot()["weather"] == before["weather"]


def scenario():
    now = datetime(2026, 9, 19, 19, tzinfo=UTC).timestamp()
    contracts = []
    for i, (lower, upper) in enumerate([(None, 68), (69, 70), (71, None)]):
        contracts.append(
            {
                "venue": "kalshi",
                "station_id": "KNYC",
                "local_day": "2026-09-19",
                "yes_token_id": f"test-{i}",
                "no_token_id": f"test-{i}:NO",
                "condition_id": f"test-{i}",
                "label": str(i),
                "lower": lower,
                "upper": upper,
                "received_at": now - 10,
                "settlement_source": "weather_company",
                "parse_status": "parsed",
                "ambiguities_json": "[]",
                "fee_known": True,
                "fee_rate": 0.07,
                "fee_exponent": 1,
                "fee_rounding": "ceil_cent",
                "tick_size": "0.01",
                "minimum_order_size": 0.01,
                "quantity_step": "0.01",
                "active": True,
                "closed": False,
                "accepting_orders": True,
                "timezone": "America/New_York",
                "observation_end": "2026-09-20T05:00:00+00:00",
            }
        )
    weather = {
        "station": "KNYC",
        "day": "2026-09-19",
        "received_at": now,
        "data_cutoff": now - 1,
        "p_end": 0.95,
        "floor": 69,
        "previous_floor": 68,
        "model_trained_at": now - 86400,
        "is_high": True,
        "model_version": "knyc-test-v1",
        "settlement_source": "weather_company",
        "observation_received_at": now - 2,
        "probabilities": {"test-0": 0.03, "test-1": 0.92, "test-2": 0.05},
    }
    books = {
        c["yes_token_id"]: {
            "bids": [[0.3, 5]],
            "asks": [[0.4, 5]],
            "complete": True,
            "received_at": now - 1,
        }
        for c in contracts
    }
    return now, contracts, weather, books


def test_frozen_targets_and_fail_closed():
    now, contracts, weather, books = scenario()
    assert [x["token"] for x in evaluate("S1", contracts, weather, books, now)["legs"]] == [
        "test-1",
        "test-2",
    ]
    for name in ("S2", "S3"):
        assert evaluate(name, contracts, weather, books, now)["legs"][0]["token"] == "test-1"
    broken = copy.deepcopy(books)
    broken.pop("test-2")
    rejected = evaluate("S1", contracts, weather, broken, now)
    assert rejected["triggered"] and rejected["action"] == "no-trade" and not rejected["legs"]
    for changed in (
        {"data_cutoff": now + 1},
        {"model_version": "klga-v1"},
        {"settlement_source": "nws_cli"},
        {"received_at": now + 1},
        {"model_trained_at": now + 1},
    ):
        result = evaluate("S3", contracts, weather | changed, books, now)
        assert not result["triggered"] and not result["legs"]


def test_costs_depth_and_first_trigger():
    now, contracts, weather, books = scenario()
    assert evaluate("S2", contracts, weather | {"is_high": False}, books, now)["triggered"] is False
    books["test-1"]["asks"] = [[0.95, 5]]
    result = evaluate("S3", contracts, weather, books, now)
    assert result["reason"] == "NO_NET_EDGE" and result["triggered"]
    books["test-1"]["asks"] = [[0.4, 0.5]]
    assert evaluate("S3", contracts, weather, books, now)["reason"] == "INSUFFICIENT_DEPTH"
    books["test-1"]["received_at"] = now + 1
    assert evaluate("S3", contracts, weather, books, now)["reason"] == "MISSING_OR_STALE_BOOK"


def test_feed_history_pagination_and_raw_versions(tmp_path):
    store = FeedStore(tmp_path / "feed.sqlite3")
    store.capture("metar", "https://example.test", 1, 2, b"one")
    store.capture("metar", "https://example.test", 3, 4, b"one")
    for i in range(4):
        store.publish("book", "kalshi:test", {"received_at": i, "bids": [], "asks": []}, i)
    page = store.history("kalshi:test", limit=2)
    older = store.history("kalshi:test", before=page[0]["seq"], limit=2)
    assert [r["time"] for r in older + page] == [0, 1, 2, 3]
    assert len(store.since(0, 2)) == 2


def test_kalshi_no_bid_is_yes_ask():
    b = normalize_book(
        "kalshi",
        {"orderbook_fp": {"yes_dollars": [[".20", "5"]], "no_dollars": [[".7", "3"]]}},
        time.time(),
    )
    assert b["asks"] == [[0.3, 3]]
    with pytest.raises(ValueError):
        normalize_book(
            "kalshi",
            {"orderbook_fp": {"yes_dollars": [[".40", "5"]], "no_dollars": [[".7", "3"]]}},
            time.time(),
        )


def test_native_strategies_recover_without_second_trigger():
    from nice_weather.trading.engine import Session
    from nice_weather.trading.recovery import native_state, restore
    from nice_weather.trading.worker import run_config

    now, contracts, weather, books = scenario()
    session = Session(
        run_config("sandbox-kalshi-knyc", "sandbox", "S1_S2_S3")
        | {
            "venue": "kalshi",
            "station_id": "KNYC",
            "execution_version": 3,
            "projection_version": 2,
        }
    )
    try:
        for c in contracts:
            session.apply({"kind": "contract", "ts": int((now - 10) * 1e9), "data": c})
        for token, book in books.items():
            session.apply(
                {
                    "kind": "depth",
                    "ts": int((now - 1) * 1e9),
                    "data": {
                        "token_id": token,
                        "received_ns": int((now - 1) * 1e9),
                        "valid": True,
                        "bids": book["bids"],
                        "asks": book["asks"],
                    },
                }
            )
        session.apply({"kind": "start", "ts": int(now * 1e9), "data": {}})
        session.apply({"kind": "weather_signal", "ts": int(now * 1e9) + 1, "data": weather})
        before = session.snapshot()
        assert set(before["signals"]) == {"S1", "S2", "S3"}
        assert len(before["fills"]) == 4
        assert {o["owner"] for o in before["orders"]} == {"S1", "S2", "S3"}
        recovered = restore(native_state(session))
        try:
            recovered.apply({"kind": "weather_signal", "ts": int((now + 1) * 1e9), "data": weather})
            assert len(recovered.snapshot()["fills"]) == 4
            assert recovered.snapshot()["cash"] == before["cash"]
        finally:
            recovered.dispose()
    finally:
        session.dispose()


def test_api_authentication_csrf_and_disabled_live(tmp_path):
    from fastapi.testclient import TestClient

    from nice_weather.trading.api import create_app

    origin = "http://testserver"
    with TestClient(create_app(tmp_path, password="test-only", origin=origin)) as client:
        assert client.get("/api/snapshot").status_code == 401
        assert client.post("/api/login", json={"password": "test-only"}).status_code == 403
        response = client.post(
            "/api/login", headers={"Origin": origin}, json={"password": "test-only"}
        )
        csrf = response.json()["csrf"]
        assert "httponly" in response.headers["set-cookie"].lower()
        assert client.get("/api/snapshot").status_code == 200
        body = {"request_id": "test", "venue": "kalshi", "mode": "live", "kind": "order"}
        assert client.post("/api/commands", json=body).status_code == 403
        assert (
            client.post(
                "/api/commands", json=body, headers={"Origin": origin, "X-CSRF-Token": csrf}
            ).status_code
            == 409
        )
        replay = {
            "request_id": "replay-1",
            "venue": "kalshi",
            "mode": "backtest",
            "kind": "backtest",
            "payload": {"start": 1, "end": 2},
        }
        headers = {"Origin": origin, "X-CSRF-Token": csrf}
        assert client.post("/api/commands", json=replay, headers=headers).status_code == 202
        assert client.post("/api/commands", json=replay, headers=headers).status_code == 202
        replay["payload"]["end"] = 3
        assert client.post("/api/commands", json=replay, headers=headers).status_code == 409


def test_partial_first_leg_stops_s1_and_survives_restart():
    from nice_weather.trading.engine import Session
    from nice_weather.trading.recovery import native_state, restore
    from nice_weather.trading.worker import run_config

    now, contracts, weather, books = scenario()
    session = Session(
        run_config("sandbox-kalshi-knyc", "sandbox", "S1")
        | {"venue": "kalshi", "station_id": "KNYC", "execution_version": 3, "projection_version": 2}
    )
    try:
        for c in contracts:
            session.apply({"kind": "contract", "ts": int((now - 10) * 1e9), "data": c})
        for token, book in books.items():
            session.apply(
                {
                    "kind": "depth",
                    "ts": int((now - 1) * 1e9),
                    "data": {
                        "token_id": token,
                        "received_ns": int((now - 1) * 1e9),
                        "valid": True,
                        "bids": book["bids"],
                        "asks": [[0.4, 1]],
                    },
                }
            )
        # Another account action consumes displayed liquidity before the basket.
        session.apply(
            {
                "kind": "order",
                "ts": int(now * 1e9),
                "request_id": "manual-first",
                "data": {
                    "token": "test-1",
                    "side": "BUY",
                    "price": 0.4,
                    "quantity": 0.5,
                    "tif": "IOC",
                },
            }
        )
        session.apply({"kind": "start", "ts": int(now * 1e9) + 1, "data": {}})
        session.apply({"kind": "weather_signal", "ts": int(now * 1e9) + 2, "data": weather})
        snapshot = session.snapshot()
        s1 = [o for o in snapshot["orders"] if o["owner"] == "S1"]
        assert len(s1) == 1 and s1[0]["filled"] == 0.5
        assert snapshot["signals"]["S1"]["execution_reason"] == "PARTIAL_OR_UNFILLED_STOPPED_BASKET"
        recovered = restore(native_state(session))
        try:
            recovered.apply({"kind": "weather_signal", "ts": int((now + 1) * 1e9), "data": weather})
            assert len(recovered.snapshot()["orders"]) == 2
        finally:
            recovered.dispose()
    finally:
        session.dispose()

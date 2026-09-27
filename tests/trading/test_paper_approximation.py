from copy import deepcopy

import pytest
from test_knyc_terminal import scenario

from nice_weather.trading.engine import Session
from nice_weather.trading.paper_execution import VERSION, ingest, preview, select
from nice_weather.trading.recovery import native_state, restore
from nice_weather.trading.worker import run_config


def make_session(venue="kalshi", **config):
    now, contracts, _, _ = scenario()
    session = Session(
        run_config("sandbox-test", "sandbox", "S3")
        | {
            "venue": venue,
            "station_id": "KNYC",
            "execution_version": 3,
            "execution_model": VERSION,
            "projection_version": 2,
            **config,
        }
    )
    for row in contracts:
        session.apply(
            {
                "kind": "contract",
                "ts": int(now * 1e9),
                "data": row | {"venue": venue, "fee_known": False},
            }
        )
    return session


def quote(session, bid=0.3, ask=0.4, **extra):
    return session.apply(
        {
            "kind": "market_price",
            "ts": session.now + 1,
            "data": {
                "token_id": "test-1",
                "received_at": session.now / 1e9,
                "best_bid": bid,
                "best_ask": ask,
                **extra,
            },
        }
    )


def order(session, request="buy", side="BUY", price=0.5, quantity=30, tif="IOC", **extra):
    return session.apply(
        {
            "kind": "order",
            "ts": session.now + 1,
            "request_id": request,
            "data": {
                "token": "test-1",
                "side": side,
                "price": price,
                "quantity": quantity,
                "tif": tif,
                **extra,
            },
        }
    )


@pytest.mark.parametrize("venue", ["kalshi", "poly_us"])
def test_native_roundtrip_fees_stale_quote_and_recovery(venue):
    session = make_session(venue)
    recovered = None
    try:
        quote(session, bid=None)
        snapshot = order(session)
        assert not snapshot["rejections"]
        assert snapshot["cash"] == pytest.approx(87.88)
        assert snapshot["positions"][0]["quantity"] == 30
        assert snapshot["fills"][0]["execution"]["price_source"] == "ask"
        # Neither model readiness nor a 30-second quote TTL blocks manual simulation.
        session.apply({"kind": "clock", "ts": session.now + 60_000_000_000})
        snapshot = order(session, "buy")
        assert len(snapshot["fills"]) == 1
        quote(session, bid=0.6, ask=0.7)
        snapshot = order(session, "sell", "SELL", 0.5, 10)
        assert snapshot["cash"] == pytest.approx(93.82)
        assert snapshot["positions"][0]["quantity"] == 20
        recovered = restore(native_state(session))
        assert recovered.snapshot()["cash"] == snapshot["cash"]
        assert recovered.snapshot()["market_prices"] == snapshot["market_prices"]
        assert len(order(recovered, "buy")["fills"]) == 2
        snapshot = order(recovered, "sell-rest", "SELL", 0.5, 20)
        assert snapshot["positions"] == []
        assert snapshot["cash"] == pytest.approx(105.70)
        assert snapshot["total_pnl"] == pytest.approx(5.70)
        assert snapshot["fees"] == pytest.approx(0.30)
    finally:
        session.dispose()
        if recovered:
            recovered.dispose()


@pytest.mark.parametrize(
    "tif,status", [("IOC", "CANCELED"), ("FOK", "CANCELED"), ("GTC", "ACCEPTED")]
)
def test_limit_semantics_and_reserves(tif, status):
    session = make_session()
    try:
        quote(session)
        snapshot = order(session, price=0.2, tif=tif)
        assert snapshot["orders"][0]["status"] == status
        assert snapshot["fills"] == []
        if tif == "GTC":
            assert snapshot["reserved"] == pytest.approx(6.06)
            quote(session, bid=0.1, ask=0.15)
            snapshot = session.snapshot()
            assert snapshot["orders"][0]["status"] == "FILLED"
            assert snapshot["fills"][0]["price"] == 0.15
            assert snapshot["reserved"] == 0
    finally:
        session.dispose()


def test_cash_shares_settings_stop_and_restart():
    session = make_session(simulation={"slippage_pp": 2})
    recovered = None
    try:
        quote(session)
        snapshot = order(session, price=0.41, tif="FOK")
        assert not snapshot["fills"]
        snapshot = order(session, "fill", price=0.45)
        assert snapshot["fills"][0]["price"] == 0.42
        assert (
            "Insufficient cash"
            in order(session, "too-big", quantity=1000)["rejections"][-1]["reason"]
        )
        assert (
            "Naked sell" in order(session, "oversell", "SELL", 0.1, 31)["rejections"][-1]["reason"]
        )
        order(session, "rest", price=0.1, tif="GTC")
        session.apply({"kind": "stop", "ts": session.now + 1, "data": {}})
        assert session.open_orders()
        recovered = restore(native_state(session))
        assert not recovered.open_orders()
        assert recovered.snapshot()["reserved"] == 0
        assert recovered.snapshot()["cash"] == session.snapshot()["cash"]
        assert len(recovered.snapshot()["fills"]) == 1
    finally:
        session.dispose()
        if recovered:
            recovered.dispose()


def test_price_evidence_fallback_and_future_identity_rejection():
    session = make_session()
    try:
        row = session.metadata["test-1"]
        now = session.now / 1e9
        prices = ingest({}, {"received_at": now, "mid": 0.4}, row, now)
        assert select(prices, "BUY", now)["price_source"] == "mid"
        prices = ingest(prices, {"received_at": now, "last_trade": 0.45}, row, now)
        assert select(prices, "BUY", now)["price_source"] == "last_trade"
        prices = ingest({}, {"received_at": now, "best_bid": 0.3}, row, now)
        assert select(prices, "BUY", now)["price_source"] == "last_quote"
        old = deepcopy(prices)
        prices = ingest(prices, {"received_at": now + 10, "valid": False}, row, now + 10)
        assert select(prices, "BUY", now + 10)["received_at"] == now
        with pytest.raises(ValueError, match="Future"):
            ingest(old, {"received_at": now + 100, "best_ask": 0.1}, row, now)
        with pytest.raises(ValueError, match="mismatch"):
            ingest(old, {"received_at": now, "venue": "poly_us"}, row, now)
        with pytest.raises(ValueError, match="NO_MARKET_PRICE"):
            select({}, "BUY", now)
        payload = {"token": "test-1", "side": "BUY", "quantity": 1, "price": 0.5}
        assert not preview(session.config, session.metadata, {}, session.snapshot(), payload, now)[
            "available"
        ]
    finally:
        session.dispose()


@pytest.mark.parametrize("order_type", ["LIMIT", "MARKET"])
def test_api_preview_queue_account_and_equity(tmp_path, order_type):
    from fastapi.testclient import TestClient
    from paper_browser_app import seed

    from nice_weather.trading.api import create_app
    from nice_weather.trading.us_runtime import paper

    seed(tmp_path)
    paper(tmp_path, "kalshi", once=True)
    client = TestClient(create_app(tmp_path, password="test", origin="http://testserver"))
    login = client.post(
        "/api/login", json={"password": "test"}, headers={"origin": "http://testserver"}
    ).json()
    headers = {"origin": "http://testserver", "x-csrf-token": login["csrf"]}
    command = {
        "request_id": "roundtrip",
        "venue": "kalshi",
        "mode": "sandbox",
        "kind": "order",
        "payload": {
            "token": "kalshi-sample-0",
            "side": "BUY",
            "quantity": 30,
            "price": 0.5,
            "tif": "IOC",
        },
    }
    if order_type == "MARKET":
        command["payload"].pop("price")
        command["payload"]["order_type"] = order_type
    check = client.post("/api/paper/preview", json=command, headers=headers).json()
    assert check["available"] and check["estimated_fee"] == 0.12
    assert client.post("/api/commands", json=command, headers=headers).status_code == 202
    paper(tmp_path, "kalshi", once=True)
    assert client.get("/api/requests/roundtrip").json()["status"] == "accepted"
    # Retry traverses both queue and process recovery without a second fill.
    assert client.post("/api/commands", json=command, headers=headers).status_code == 202
    paper(tmp_path, "kalshi", once=True)
    snapshot = client.get("/api/snapshot").json()["accounts"][0]["snapshot"]
    assert snapshot["orders"][0]["order_type"] == order_type
    assert snapshot["cash"] == 87.88
    assert len(snapshot["fills"]) == 1
    command["payload"]["quantity"] = 31
    assert client.post("/api/commands", json=command, headers=headers).status_code == 409
    points = client.get("/api/paper/equity?run_id=sandbox-kalshi-knyc").json()["points"]
    assert any(p["cash"] == 87.88 and p["market_value"] == 9 for p in points)


def test_candidates_stop_settlement_and_historical_clock():
    from datetime import UTC, datetime

    from nice_weather.trading.storage import digest

    sessions = [make_session(mode=mode) for mode in ("sandbox", "backtest")]
    try:
        for session in sessions:
            quote(session)
            session.apply({"kind": "start", "ts": session.now + 1, "data": {}})
            payload = {
                "token": "test-1",
                "side": "BUY",
                "quantity": 1,
                "price": 0.5,
                "strategy": "S3",
                "signal_id": "historical-sample",
                "tif": "IOC",
            }
            snapshot = session.apply(
                {
                    "kind": "candidate_order",
                    "ts": session.now + 1,
                    "request_id": "candidate",
                    "data": payload,
                }
            )
            assert snapshot["fills"][0]["execution"]["owner"] == "S3"
            session.apply({"kind": "stop", "ts": session.now + 1, "data": {}})
            snapshot = session.apply(
                {
                    "kind": "candidate_order",
                    "ts": session.now + 1,
                    "request_id": "stopped",
                    "data": payload,
                }
            )
            assert "stopped" in snapshot["rejections"][-1]["reason"]
            received = session.now / 1e9 + 3600
            proof = {
                "market": {
                    "ticker": "test-1",
                    "status": "finalized",
                    "result": "yes",
                    "settlement_ts": datetime.fromtimestamp(received, UTC).isoformat(),
                    "settlement_value_dollars": "1.0000",
                }
            }
            session.apply(
                {
                    "kind": "settlement",
                    "ts": int(received * 1e9),
                    "data": {
                        "token_id": "test-1",
                        "value": 1,
                        "evidence_type": "official_final",
                        "source_hash": digest(proof),
                        "source_payload": proof,
                        "received_at": received,
                    },
                }
            )
            assert session.snapshot()["cash"] == pytest.approx(100.59)
        for key in ("cash", "equity", "fees", "positions", "fills"):
            assert sessions[0].snapshot()[key] == sessions[1].snapshot()[key]
    finally:
        for session in sessions:
            session.dispose()


def test_known_fees_gtc_fill_and_explicit_source_time():
    from nice_weather.trading.us_runtime import feed_event

    session = make_session(mode="backtest")
    try:
        session.metadata["test-1"]["fee_known"] = True
        quote(session)
        order(session, quantity=1, price=0.2, tif="GTC")
        quote(session, bid=0.1, ask=0.15)
        fill = session.snapshot()["fills"][0]
        assert fill["fee"] == 0.01
        assert not fill["execution"]["fee_estimated"]
        row = {
            "kind": "market_price",
            "key": "test-1",
            "received": None,
            "time_basis": "source_time",
            "source_time": session.now / 1e9 + 1,
            "data": {"best_ask": 0.3},
        }
        for event in feed_event(session, row):
            session.apply(event)
        selected = session.approximate_price("test-1", "BUY")
        assert selected["received_at"] is None and selected["time_basis"] == "source_time"
        session.config["mode"] = "sandbox"
        with pytest.raises(ValueError, match="Missing received"):
            feed_event(session, row)
    finally:
        session.dispose()


def test_existing_paper_upgrade_keeps_native_fills_and_cash(tmp_path):
    import json

    from nice_weather.trading.recovery import PaperRunner
    from nice_weather.trading.storage import Results
    from nice_weather.trading.us_runtime import paper

    session = make_session(execution_model=None, account="sandbox-kalshi-knyc")
    session.metadata["test-1"]["fee_known"] = True
    session.apply(
        {
            "kind": "depth",
            "ts": session.now + 1,
            "data": {
                "token_id": "test-1",
                "valid": True,
                "bids": [[0.3, 5]],
                "asks": [[0.4, 5]],
                "received_ns": session.now,
            },
        }
    )
    before = order(session, quantity=1)
    assert len(before["fills"]) == 1
    results = Results(tmp_path / "results.sqlite3")
    account = session.config["account"]
    results.create(account, account, "sandbox", session.config)
    runner = PaperRunner(results, results.run(account=account))
    runner.session.dispose()
    runner.session = session
    runner.commit("existing-native-fill")
    session.dispose()
    paper(tmp_path, "kalshi", once=True)
    after = json.loads(results.run(account=account)["snapshot"])
    assert after["execution_model"] == VERSION
    assert after["cash"] == before["cash"]
    assert len(after["fills"]) == 1
    assert after["fills"][0]["price"] == before["fills"][0]["price"]
    assert after["fills"][0]["execution"] is None


@pytest.mark.parametrize("venue", ["kalshi", "poly_us"])
@pytest.mark.parametrize("outcome", ["YES", "NO"])
@pytest.mark.parametrize("tif", ["IOC", "FOK"])
def test_market_native_roundtrip_and_recovery(venue, outcome, tif):
    session = make_session(venue, simulation={"slippage_pp": 2})
    recovered = None
    token = "test-1" + (":NO" if outcome == "NO" else "")
    payload = {"token": token, "side": "BUY", "quantity": 30, "order_type": "MARKET", "tif": tif}
    try:
        quote(session, token_id=token)
        check = preview(session.config, session.metadata, session.market_prices,
                        session.snapshot(), payload, session.now / 1e9)
        assert check["available"] and check["order_type"] == "MARKET"
        assert check["selected_price"]["price"] == 0.42
        snapshot = session.apply({"kind": "order", "ts": session.now + 1,
                                  "request_id": "market-buy", "data": payload})
        assert not snapshot["rejections"]
        submitted = snapshot["orders"][0]
        assert (submitted["order_type"], submitted["tif"], submitted["price"]) == (
            "MARKET", tif, None)
        assert submitted["status"] == "FILLED"
        fill = snapshot["fills"][0]
        assert fill["token"] == token and fill["price"] == check["selected_price"]["price"]
        assert fill["fee"] == check["estimated_fee"]
        assert fill["execution"]["price_source"] == "ask"
        assert snapshot["cash"] == pytest.approx(87.27)
        assert snapshot["reserved"] == 0
        recovered = restore(native_state(session))
        again = recovered.apply({"kind": "order", "ts": recovered.now + 1,
                                 "request_id": "market-buy", "data": payload})
        assert again["orders"] == snapshot["orders"] and again["fills"] == snapshot["fills"]
        quote(recovered, bid=0.6, ask=0.7, token_id=token)
        sold = recovered.apply({"kind": "order", "ts": recovered.now + 1,
                                "request_id": "market-sell", "data": payload | {"side": "SELL"}})
        assert not sold["positions"] and len(sold["fills"]) == 2
        assert sold["fills"][-1]["price"] == 0.58
        assert sold["cash"] == pytest.approx(104.49)
        assert sold["fees"] == pytest.approx(0.31)
        conflict = recovered.apply({"kind": "order", "ts": recovered.now + 1,
                                    "request_id": "market-buy",
                                    "data": payload | {"quantity": 31}})
        assert "different payload" in conflict["rejections"][-1]["reason"]
        assert len(conflict["fills"]) == 2
    finally:
        session.dispose()
        if recovered:
            recovered.dispose()


def test_market_validation_rechecks_quote_and_reserved_funds():
    session = make_session()
    payload = {"token": "test-1", "side": "BUY", "quantity": 200, "order_type": "MARKET"}

    def check(values):
        return preview(session.config, session.metadata, session.market_prices,
                       session.snapshot(), values, session.now / 1e9)

    def submit(request, values):
        return session.apply({"kind": "order", "ts": session.now + 1,
                              "request_id": request, "data": values})

    try:
        assert "NO_MARKET_PRICE" in check(payload)["reason"]
        quote(session)
        assert check(payload)["available"] and check(payload)["tif"] == "IOC"
        for invalid in ({"tif": "GTC"}, {"price": 0.5}, {"order_type": "STOP"},
                        {"order_type": []}, {"quantity": True}, {"quantity": -1}):
            assert not check(payload | invalid)["available"]
        assert "Naked sell" in check(payload | {"side": "SELL"})["reason"]
        # The earlier affordable preview must not authorize execution after a price change.
        quote(session, bid=0.5, ask=0.6)
        snapshot = submit("more-expensive", payload)
        assert "Insufficient cash" in snapshot["rejections"][-1]["reason"]
        assert not snapshot["fills"]
        quote(session)
        order(session, "resting", quantity=400, price=0.2, tif="GTC")
        snapshot = submit("reserved", payload | {"quantity": 50})
        assert "Insufficient cash" in snapshot["rejections"][-1]["reason"]
        assert not snapshot["fills"] and snapshot["reserved"] == pytest.approx(80.8)
        # The approved approximation keeps a one-sided quote's actual provenance.
        quote(session, bid=None, ask=0.4)
        single_side = check(payload | {"quantity": 1})
        assert single_side["available"] and single_side["selected_price"]["price_source"] == "ask"
    finally:
        session.dispose()

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from test_knyc_terminal import scenario

from nice_weather.trading.us_markets import final_value


def test_terminal_snapshot_excludes_old_books_but_preserves_history(tmp_path):
    from nice_weather.trading.feed import FeedStore

    store = FeedStore(tmp_path / "feed.sqlite3")
    store.publish("contracts", "kalshi", [{"yes_token_id": "old"}])
    store.publish("book", "old", {"bids": [[.2, 1]], "asks": [[.3, 1]]})
    store.publish("contracts", "kalshi", [{"yes_token_id": "current"}])
    store.publish("book", "current", {"bids": [[.2, 1]], "asks": [[.3, 1]]})
    assert set(store.snapshot()["books"]) == {"current"}
    assert len(store.history("old")) == 1


def test_poly_requires_final_status_confirmation_and_matching_prices():
    contract = {"venue": "poly_us", "condition_id": "market"}
    evidence = {"market": {"slug": "market", "status": "MARKET_STATUS_RESOLVED",
                           "ep3Status": "EXPIRED", "closed": True,
                           "outcomes": '["Yes","No"]', "outcomePrices": '[".4",".6"]'},
                "confirmation": {"slug": "market", "settlement": .4}}
    assert final_value(contract, evidence, 100) == Decimal(".4")
    evidence["market"]["status"] = "MARKET_STATUS_CLOSED"
    with pytest.raises(ValueError, match="not final"):
        final_value(contract, evidence, 100)
    evidence["market"]["status"] = "MARKET_STATUS_RESOLVED"
    evidence["confirmation"]["settlement"] = 1
    with pytest.raises(ValueError, match="Inconsistent"):
        final_value(contract, evidence, 100)


def test_native_us_settlement_closes_position_once_and_recovers():
    from nice_weather.trading.engine import Session
    from nice_weather.trading.recovery import native_state, restore
    from nice_weather.trading.us_runtime import feed_event
    from nice_weather.trading.worker import run_config

    now, contracts, weather, books = scenario()
    session = Session(run_config("test", "sandbox", "S3") | {
        "venue": "kalshi", "station_id": "KNYC", "execution_version": 3,
        "signal_version": "knyc-executable-v2"})
    recovered = None
    try:
        for contract in contracts:
            session.apply({"kind": "contract", "ts": int((now - 10) * 1e9), "data": contract})
        session.apply({"kind": "start", "ts": int(now * 1e9), "data": {}})
        session.apply({"kind": "weather_signal", "ts": int(now * 1e9) + 1, "data": weather})
        row = {"kind": "book", "key": "test-1", "received": now + 1,
               "data": books["test-1"]}
        for event in feed_event(session, row):
            session.apply(event)
        assert len(session.snapshot()["fills"]) == 1
        received = now + 3600
        proof = {"market": {"ticker": "test-1", "status": "finalized", "result": "yes",
                             "settlement_ts": datetime.fromtimestamp(received, UTC).isoformat(),
                             "settlement_value_dollars": "1.0000"}}
        row = {"kind": "settlement", "key": "test-1", "received": received,
               "data": {"source_payload": proof, "capture_ids": [1]}}
        for event in feed_event(session, row):
            session.apply(event)
        snapshot = session.snapshot()
        assert snapshot["cash"] == pytest.approx(100.58)
        assert session.outcomes == {"test-1": 1.0, "test-1:NO": 0.0}
        assert feed_event(session, row) == []
        recovered = restore(native_state(session))
        assert recovered.snapshot()["cash"] == snapshot["cash"]
        assert feed_event(recovered, row) == []
        proof["market"].update(result="no", settlement_value_dollars="0.0000")
        with pytest.raises(ValueError, match="changed"):
            feed_event(recovered, row)
    finally:
        session.dispose()
        if recovered:
            recovered.dispose()


@pytest.mark.parametrize("venue", ["poly_us", "kalshi"])
@pytest.mark.parametrize("open_no_quantity", [0, 3])
@pytest.mark.parametrize("order_type", ["LIMIT", "MARKET"])
def test_settlement_after_restoring_flat_position_preserves_native_facts(
    venue, open_no_quantity, order_type
):
    from test_paper_approximation import make_session, order, quote

    from nice_weather.trading.recovery import native_state, restore
    from nice_weather.trading.us_runtime import feed_event

    session = make_session(venue)
    sessions = [session]

    def trade(request, side, quantity, token="test-1"):
        payload = {"token": token, "side": side, "quantity": quantity,
                   "order_type": order_type, "tif": "IOC"}
        if order_type == "LIMIT":
            payload["price"] = 0.5
        session.apply({"kind": "order", "ts": session.now + 1,
                       "request_id": request, "data": payload})

    try:
        quote(session)
        trade("buy", "BUY", 2)
        quote(session, bid=0.6, ask=0.7)
        trade("exit", "SELL", 2)
        if open_no_quantity:
            quote(session, token_id="test-1:NO")
            trade("no-buy", "BUY", open_no_quantity, "test-1:NO")
        before = session.snapshot()
        state = native_state(session)
        assert len(session.engine.cache.positions_closed()) == 1
        recovered = restore(state)
        sessions.append(recovered)
        cache = recovered.engine.cache
        # A restored FLAT position must stay in history without entering the open index.
        assert len(cache.positions_closed()) == 1
        assert len(cache.positions_open()) == bool(open_no_quantity)
        assert all(position.is_open for position in cache.positions_open())
        for key in ("cash", "fills", "orders", "positions"):
            assert recovered.snapshot()[key] == before[key]
        assert native_state(recovered)["positions"] == state["positions"]
        assert native_state(recovered)["archived"] == state["archived"]

        # Expiration must also cancel a newly resting order without inventing a fill.
        order(recovered, "resting", quantity=1, price=0.1, tif="GTC")
        assert recovered.open_orders()
        received = recovered.now / 1e9 + 3600
        if venue == "poly_us":
            proof = {
                "market": {"slug": "test-1", "status": "MARKET_STATUS_RESOLVED",
                           "ep3Status": "EXPIRED", "closed": True,
                           "outcomes": '["Yes","No"]', "outcomePrices": '[".4",".6"]'},
                "confirmation": {"slug": "test-1", "settlement": .4},
            }
            yes_payout = .4
        else:
            proof = {"market": {
                "ticker": "test-1", "status": "finalized", "result": "no",
                "settlement_ts": datetime.fromtimestamp(received, UTC).isoformat(),
                "settlement_value_dollars": "0.0000",
            }}
            yes_payout = 0
        row = {"kind": "settlement", "key": "test-1", "received": received,
               "data": {"source_payload": proof, "capture_ids": [1]}}
        for event in feed_event(recovered, row):
            recovered.apply(event)
        settled = recovered.snapshot()
        assert settled["cash"] == pytest.approx(
            before["cash"] + open_no_quantity * (1 - yes_payout))
        assert len(settled["fills"]) == len(before["fills"]) + bool(open_no_quantity)
        assert settled["fees"] == before["fees"]
        expiration_orders = [order for order in cache.orders()
                             if str(order.client_order_id).startswith("EXPIRATION-")]
        assert len(expiration_orders) == bool(open_no_quantity)
        for expiration in expiration_orders:
            assert [type(event).__name__ for event in expiration.events] == [
                "OrderInitialized", "OrderSubmitted", "OrderAccepted", "OrderFilled"]
            assert expiration.account_id == cache.account_for_venue(recovered.venue).id
            assert float(expiration.events[-1].commission) == 0
        for fill in settled["fills"]:
            if fill["order_id"].startswith("settlement-"):
                assert fill["ts"] == int(received * 1e9) + 1  # NO follows YES in feed_event.
                assert fill["price"] == 1 - yes_payout
        assert not settled["positions"] and not recovered.open_orders()
        assert settled["reserved"] == 0
        assert settled["settled"] == {"test-1": yes_payout, "test-1:NO": 1 - yes_payout}
        assert not cache.positions_open()
        assert len(cache.positions_closed()) == 1 + bool(open_no_quantity)
        assert feed_event(recovered, row) == []

        again = restore(native_state(recovered))
        sessions.append(again)
        assert not again.engine.cache.positions_open()
        assert len(again.engine.cache.positions_closed()) == 1 + bool(open_no_quantity)
        assert feed_event(again, row) == []
        for key in ("cash", "fills", "orders", "positions", "fees", "settled"):
            assert again.snapshot()[key] == settled[key]
        assert len(again.settlements) == 2
    finally:
        for current in sessions:
            current.dispose()

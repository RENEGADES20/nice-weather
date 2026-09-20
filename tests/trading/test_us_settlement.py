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

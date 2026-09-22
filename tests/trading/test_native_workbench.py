from copy import deepcopy

import pytest
from nautilus_trader.model.enums import OrderSide

from nice_weather.trading.dataset import timestamp
from nice_weather.trading.engine import Session
from nice_weather.trading.storage import Requests, Results, connect, digest
from nice_weather.trading.worker import Runner, run_config

T = timestamp("2026-09-12T12:00:00+00:00")


def contract(**changes):
    return (
        dict(
            yes_token_id="111",
            no_token_id="222",
            condition_id="condition",
            tick_size=0.01,
            label="80 F",
            local_day="2026-09-12",
            station_id="KLGA",
            timezone="America/New_York",
            parse_status="parsed",
            ambiguities_json="[]",
            closed=False,
            active=True,
            accepting_orders=True,
            observation_end="2026-09-13T04:00:00+00:00",
            minimum_order_size=1,
            fee_rate=0,
            fee_exponent=1,
            fee_known=True,
        )
        | changes
    )


def quote(token="111", **changes):
    return (
        dict(
            token_id=token,
            best_bid=0.39,
            best_ask=0.4,
            bid_size=10,
            ask_size=2,
            source="clob_ws",
            event_kind="snapshot",
            status="available",
        )
        | changes
    )


def event(kind, offset, data=None, request_id=None):
    if kind == "settlement":
        # Fixed official-response shape, not an observed natural settlement.
        raw = {
            "closed": True,
            "condition_id": "condition",
            "tokens": [
                {
                    "token_id": t,
                    "winner": bool(data["value"])
                    if t == data["token_id"]
                    else not bool(data["value"]),
                }
                for t in ("111", "222")
            ],
        }
        data = data | {"source_payload": raw, "source_hash": digest(raw)}
    return dict(kind=kind, ts=T + offset, data=data or {}, request_id=request_id or f"cmd-{offset}")


def buy(**changes):
    return dict(token="111", side="BUY", quantity=3, price=0.4, tif="IOC") | changes


@pytest.fixture
def session():
    s = Session(run_config("sandbox-test", "sandbox"))
    s.apply(event("contract", 0, contract()))
    s.apply(event("quote", 1, quote()))
    yield s
    s.dispose()


def test_partial_fill_liquidity_and_duplicate_request(session):
    result = session.apply(event("order", 2, buy()))
    assert result["cash"] == 99.2
    assert result["positions"][0]["quantity"] == 2
    assert result["equity"] == pytest.approx(99.98)
    assert result["drawdown"] == pytest.approx(0.0002)
    assert result["orders"][0]["status"] == "CANCELED"
    session.apply(event("order", 3, buy(), "cmd-2"))
    assert len(session.snapshot()["fills"]) == 1
    session.apply(event("order", 4, buy()))
    assert sum(f["quantity"] for f in session.snapshot()["fills"]) == 2


def test_yes_no_exit_fees_and_settlement(session):
    session.apply(event("contract", 2, contract(fee_rate=0.02)))
    session.apply(event("order", 3, buy(quantity=1)))
    assert session.snapshot()["fees"] == pytest.approx(0.0048)
    session.apply(event("quote", 4, quote("222", best_bid=0.59, best_ask=0.60)))
    session.apply(event("order", 5, buy(token="222", quantity=1, price=0.6)))
    assert len(session.snapshot()["positions"]) == 2
    session.apply(event("close", 6, {"token": "111"}))
    assert len(session.snapshot()["positions"]) == 1
    filled = session.depth_fills()
    assert session.depth_filled("111", OrderSide.SELL, "0.400", filled) == 1
    assert session.depth_filled("111", OrderSide.BUY, "0.39", filled) == 1
    assert session.depth_filled("222", OrderSide.SELL, "0.60", filled) == 1
    assert session.depth_filled("222", OrderSide.SELL, "0.40", filled) == 0
    result = session.apply(
        event(
            "settlement",
            7,
            {
                "token_id": "222",
                "value": 1,
                "evidence_type": "official_final",
                "source_hash": "fixed-test-evidence",
            },
        )
    )
    assert result["positions"] == []
    assert result["cash"] == pytest.approx(100.375642)


def test_zero_settlement(session):
    session.apply(event("order", 2, buy(quantity=1)))
    result = session.apply(
        event(
            "settlement",
            3,
            {
                "token_id": "111",
                "value": 0,
                "evidence_type": "official_final",
                "source_hash": "fixed-test-evidence",
            },
        )
    )
    assert result["cash"] == 99.6
    assert result["positions"] == []


def test_reopened_netting_position_preserves_lifetime_pnl_and_fees(session):
    session.apply(event("contract", 2, contract(fee_rate=0.02)))
    for offset in (3, 10):
        session.apply(event("quote", offset, quote()))
        session.apply(event("order", offset + 1, buy(quantity=1)))
        result = session.apply(event("close", offset + 2, {"token": "111"}))
    assert len(result["fills"]) == 4
    assert result["positions"] == []
    assert result["realized_pnl"] == pytest.approx(result["cash"] - 100)
    assert result["fees"] == pytest.approx(sum(f["fee"] for f in result["fills"]))
    assert result["win_rate"] == 0


def test_legacy_projection_replays_before_explicit_upgrade(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    config = run_config("sandbox-test", "sandbox")
    config.pop("projection_version")
    results.create("legacy", "sandbox-test", "sandbox", config)
    runner = Runner(results, results.run("legacy"))
    inputs = [event("contract", 0, contract(fee_rate=0.02))]
    for offset in (1, 10):
        inputs += [
            event("quote", offset, quote()),
            event("order", offset + 1, buy(quantity=1)),
            event("close", offset + 2, {"token": "111"}),
        ]
    for i, item in enumerate(inputs):
        runner.apply(str(i), item)
    old = runner.session.snapshot()
    runner.session.dispose()
    runner = Runner(results, results.run("legacy"))
    assert runner.session.snapshot() == old
    corrected = runner.apply(
        "projection-upgrade-2", event("projection_upgrade", 20, {"version": 2})
    )
    assert corrected["cash"] == old["cash"]
    assert corrected["fills"] == old["fills"]
    assert corrected["fees"] == pytest.approx(sum(f["fee"] for f in corrected["fills"]))
    runner.session.dispose()
    runner = Runner(results, results.run("legacy"))
    assert runner.session.snapshot() == corrected
    runner.session.dispose()


def test_settlement_rejects_tampered_official_proof(session):
    final = event(
        "settlement", 2, {"token_id": "111", "value": 1, "evidence_type": "official_final"}
    )
    final["data"]["source_payload"]["closed"] = False
    with pytest.raises(ValueError, match="evidence"):
        session.apply(final)
    assert session.snapshot()["settled"] == {}


def test_fok_requires_entire_depth_and_gtd_expires(session):
    result = session.apply(event("order", 2, buy(tif="FOK", quantity=3)))
    assert result["fills"] == []
    session.apply(
        event(
            "order", 3, buy(tif="GTD", price=0.38, quantity=1, expire_time="2026-09-12T12:00:01Z")
        )
    )
    result = session.apply(event("clock", 2_000_000_000))
    orders = {o["order_id"]: o for o in result["orders"]}
    # Native strategy GTD timer cancels even while no new quote arrives.
    assert orders["cmd-3"]["status"] in {"CANCELED", "EXPIRED"}
    assert result["reserved"] == 0


def test_both_quotes_required_at_decision_time():
    config = run_config(
        "backtest-test",
        "backtest",
        "acceptance_roundtrip",
        {"quantity": 1, "require_both": True},
        ["111"],
    )
    s = Session(config)
    try:
        s.apply(event("contract", 0, contract()))
        s.apply(event("start", 1))
        assert s.apply(event("quote", 2, quote()))["fills"] == []
        s.apply(event("quote", 3, quote("222", best_bid=0.59, best_ask=0.60)))
        result = s.apply(event("quote", 4, quote()))
        assert len(result["fills"]) == 1
        assert result["fills"][0]["ts"] == T + 4
    finally:
        s.dispose()


def test_cancel_replace_remaining_target(session):
    session.apply(event("order", 2, buy(tif="GTC")))
    assert session.snapshot()["orders"][0]["filled"] == 2
    session.apply(
        event(
            "replace",
            3,
            {
                "order_id": "cmd-2",
                "token": "111",
                "side": "BUY",
                "target_quantity": 4,
                "price": 0.38,
                "tif": "GTC",
            },
        )
    )
    orders = {o["order_id"]: o for o in session.snapshot()["orders"]}
    assert orders["cmd-2"]["status"] == "CANCELED"
    assert orders["cmd-3"]["quantity"] == 2
    session.apply(event("cancel", 4, {"order_id": "cmd-3"}))
    assert all(o["status"] == "CANCELED" for o in session.snapshot()["orders"])


@pytest.mark.parametrize(
    "payload,reason",
    [
        (buy(side="SELL"), "Naked"),
        (buy(quantity=1000), "cash"),
        (buy(quantity=13), "exposure"),
        (buy(price=0.401), "precision"),
        (buy(quantity=1.0000001), "precision"),
        (buy(quantity=float("nan")), "Invalid"),
        (buy(post_only=True), "post-only"),
        (buy(tif="GTD", expire_time="2026-01-01T00:00:00Z"), "expired"),
    ],
)
def test_risk_rejections(session, payload, reason):
    result = session.apply(event("order", 2, payload))
    assert not result["fills"]
    assert reason.lower() in result["rejections"][-1]["reason"].lower()


def test_stale_missing_and_ambiguous(session):
    result = session.apply(event("order", 31_000_000_001, buy()))
    assert "Stale" in result["rejections"][-1]["reason"]
    session.apply(event("quote", 32_000_000_000, quote(ask_size=None)))
    result = session.apply(event("order", 32_000_000_001, buy()))
    assert "Missing" in result["rejections"][-1]["reason"]
    session.apply(event("quote", 33_000_000_000, quote()))
    session.apply(event("contract", 33_000_000_001, contract(ambiguities_json='["rounding"]')))
    result = session.apply(event("order", 33_000_000_002, buy()))
    assert "Ambiguous" in result["rejections"][-1]["reason"]


def test_crash_replay_and_uncheckpointed_input(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", "sandbox-test", "sandbox", run_config("sandbox-test", "sandbox"))
    runner = Runner(results, results.run("run"))
    for i, ev in enumerate(
        [
            event("contract", 0, contract()),
            event("quote", 1, quote()),
            event("order", 2, buy(tif="GTC")),
        ]
    ):
        runner.apply(str(i), ev)
    before = runner.session.snapshot()
    runner.session.dispose()
    recovered = Runner(results, results.run("run"))
    assert recovered.session.snapshot() == before
    recovered.apply("2", event("order", 2, buy(tif="GTC")))
    assert recovered.session.snapshot() == before
    recovered.session.dispose()
    results.append("run", "crash", event("cancel", 3, {"order_id": "cmd-2"}))
    recovered = Runner(results, results.run("run"))
    assert recovered.session.snapshot()["orders"][0]["status"] == "CANCELED"
    recovered.session.dispose()
    with connect(results.path) as con:
        con.execute("UPDATE inputs SET snapshot_hash='tampered' WHERE seq=2")
    with pytest.raises(RuntimeError, match="Recovery mismatch"):
        Runner(results, results.run("run"))
    assert results.run("run")["status"] == "paused"


def test_request_identity_collision_and_dst(tmp_path):
    requests = Requests(tmp_path / "requests.sqlite3")
    requests.submit("same", "sandbox-test", "sandbox", "order", buy())
    requests.submit("same", "sandbox-test", "sandbox", "order", buy())
    assert len(requests.pending("sandbox-test", "sandbox")) == 1
    with pytest.raises(ValueError, match="different payload"):
        requests.submit("same", "sandbox-test", "sandbox", "order", buy(quantity=2))
    assert timestamp("2026-11-01T01:30:00-05:00") - timestamp("2026-11-01T01:30:00-04:00") == 3600e9
    with pytest.raises(ValueError, match="timezone"):
        timestamp("2026-09-12T12:00:00")


def test_future_revision_does_not_change_past(session):
    session.apply(event("order", 2, buy(quantity=1)))
    past = deepcopy(session.snapshot())
    future = contract(ambiguities_json='["future revision"]')
    session.apply(event("contract", 3, future))
    assert session.snapshot()["fills"] == past["fills"]
    assert session.snapshot()["cash"] == past["cash"]


@pytest.mark.parametrize(
    "changes",
    [
        {"ambiguities_json": '["rounding"]'},
        {"active": False},
        {"fee_known": False},
        {"observation_end": "2026-09-12T12:00:01+00:00"},
    ],
)
def test_resting_order_canceled_before_invalid_market_can_match(session, changes):
    session.apply(event("order", 2, buy(quantity=1, price=0.38, tif="GTC")))
    assert session.snapshot()["orders"][0]["status"] == "ACCEPTED"
    session.apply(event("contract", 3, contract(**changes)))
    # Keep the original quote fresh while crossing the observation boundary.
    result = session.apply(event("quote", 2_000_000_000, quote(best_bid=0.36, best_ask=0.37)))
    assert result["orders"][0]["status"] == "CANCELED"
    assert result["fills"] == []
    assert result["cash"] == 100


def test_stale_resting_order_cancellation_precedes_crossing_quote(session):
    session.apply(event("order", 2, buy(quantity=1, price=0.38, tif="GTC")))
    result = session.apply(event("quote", 31_000_000_001, quote(best_bid=0.36, best_ask=0.37)))
    assert result["orders"][0]["status"] == "CANCELED"
    assert result["fills"] == []


def test_exit_keeps_reviewed_price_when_bid_falls(session):
    session.apply(event("order", 2, buy(quantity=1)))
    session.apply(event("quote", 3, quote(best_bid=0.37, best_ask=0.38)))
    result = session.apply(event("close", 4, {"token": "111", "quantity": 1, "price": 0.39}))
    assert result["positions"][0]["quantity"] == 1
    assert len(result["fills"]) == 1
    assert result["orders"][-1]["status"] == "CANCELED"


def test_tick_revision_updates_native_precision_and_cancels_old_orders(session):
    session.apply(event("order", 2, buy(quantity=1, price=0.38, tif="GTC")))
    result = session.apply(event("contract", 3, contract(tick_size=0.001)))
    assert result["orders"][0]["status"] == "CANCELED"
    assert session.instruments["111"].price_precision == 3
    session.apply(event("quote", 4, quote(best_bid=0.381, best_ask=0.382)))
    result = session.apply(event("order", 5, buy(quantity=1, price=0.382)))
    assert result["fills"][0]["price"] == 0.382
    assert result["cash"] == pytest.approx(99.618)


def test_native_live_config_is_disabled_and_persistent():
    from nice_weather.trading.live import node_config, status

    assert status()["execution_enabled"] is False
    with pytest.raises(ValueError, match="credentials"):
        node_config(credentials={}, instrument_ids=frozenset({"condition-111.POLYMARKET"}))
    config = node_config(
        credentials=dict(
            private_key="test", api_key="test", api_secret="test", passphrase="test", funder="test"
        ),
        instrument_ids=frozenset({"condition-111.POLYMARKET"}),
    )
    assert config.cache.database.type == "redis"
    assert config.cache.persist_account_events
    assert not config.cache.flush_on_start
    assert config.exec_engine.reconciliation

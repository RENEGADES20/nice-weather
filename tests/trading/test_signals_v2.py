from decimal import Decimal
from itertools import product

import pytest
from test_knyc_terminal import scenario

from nice_weather.trading.signals_v2 import candidates, evaluate, optimize


def limits(contracts, budget=3):
    return {
        "cash": 100,
        "budget": budget,
        "day_remaining": 20,
        "loss_remaining": 20,
        "bin_remaining": {c["yes_token_id"]: 5 for c in contracts},
    }


def test_optimizer_matches_exhaustive_grid():
    now, contracts, weather, books = scenario()
    for c in contracts:
        c["quantity_step"] = "0.25"
    books["test-1"]["asks"] = [[0.4, 2], [0.6, 3]]
    rows = [
        candidates(
            c,
            books[c["yes_token_id"]],
            weather["probabilities"][c["yes_token_id"]],
            Decimal(3),
            now,
        )
        for c in contracts[1:]
    ]
    for amount in ("0.8", "1", "1.5", "2", "2.9", "3"):
        budget = Decimal(amount)
        feasible = [pair for pair in product(*rows) if sum(x["cost"] for x in pair) <= budget]
        if not feasible:
            continue
        expected = max(
            feasible,
            key=lambda pair: (
                sum(x["edge"] for x in pair),
                -sum(x["cost"] for x in pair),
                -pair[0]["quantity"],
                -pair[1]["quantity"],
            ),
        )
        assert optimize(rows, budget) == list(expected)


def test_unverified_rules_do_not_mislabel_model_or_allow_execution():
    now, contracts, weather, books = scenario()
    contracts[0]["parse_status"] = "ambiguous"
    signal = evaluate("S3", contracts, weather, books, now, limits(contracts))
    assert signal["reason"] == "CONTRACT_RULES_UNVERIFIED"
    assert signal["action"] == "no-trade" and signal["legs"] == []


def test_v2_quantity_risk_and_warning():
    now, contracts, weather, books = scenario()
    signal = evaluate("S1", contracts, weather, books, now, limits(contracts))
    assert signal["action"] == "buy"
    assert signal["legs"][0]["quantity"] > signal["legs"][1]["quantity"] >= 1
    assert signal["cost"] <= 3
    assert signal["settlement_pnl"]["test-0"] == -signal["cost"]
    risk = limits(contracts, 0.5)
    assert evaluate("S1", contracts, weather, books, now, risk)["action"] == "no-trade"
    contracts[1]["minimum_order_size"] = 2
    assert evaluate("S3", contracts, weather, books, now, risk)["reason"] == (
        "ONE_SHARE_BELOW_MARKET_MINIMUM"
    )
    warning = evaluate(
        "S2", contracts, weather | {"floor": 70, "is_high": False}, books, now, limits(contracts)
    )
    assert warning["warning"]["near_boundary"] and warning["action"] == "no-trade"


@pytest.mark.parametrize("rounding", ["ceil_cent", "poly_us_order_half_even_v1"])
def test_disabled_signal_then_quote_reassessment_and_recovery(rounding):
    from nice_weather.trading.engine import Session
    from nice_weather.trading.recovery import native_state, restore
    from nice_weather.trading.worker import run_config

    now, contracts, weather, books = scenario()
    for contract in contracts:
        contract["fee_rounding"] = rounding
    session = Session(
        run_config("test", "sandbox", "S3")
        | {
            "venue": "kalshi",
            "station_id": "KNYC",
            "execution_version": 3,
            "signal_version": "knyc-executable-v2",
        }
    )
    recovered = None
    try:
        for c in contracts:
            session.apply({"kind": "contract", "ts": int((now - 10) * 1e9), "data": c})
        session.apply({"kind": "weather_signal", "ts": int(now * 1e9), "data": weather})
        assert session.signals["S3"]["triggered"]
        assert session.signals["S3"]["action"] == "no-trade"
        assert not session.snapshot()["fills"]
        session.apply({"kind": "start", "ts": int(now * 1e9) + 1, "data": {}})
        book = books["test-1"]
        event = {
            "kind": "depth",
            "ts": int((now + 1) * 1e9),
            "data": {
                "token_id": "test-1",
                "received_ns": int((now + 1) * 1e9),
                "valid": True,
                "bids": book["bids"],
                "asks": book["asks"],
            },
        }
        session.apply(event)
        assert len(session.snapshot()["fills"]) == 1
        recovered = restore(native_state(session))
        recovered.apply(event | {"ts": event["ts"] + 1})
        assert len(recovered.snapshot()["fills"]) == 1
        assert recovered.latest_weather == weather
        assert recovered.fee_accumulators == session.fee_accumulators
        assert recovered.depth_fills() == session.depth_fills()
        # The position opportunity stays consumed, but new weather remains visible.
        later = weather | {"p_end": .7, "received_at": now + 2, "data_cutoff": now + 2}
        recovered.apply({"kind": "weather_signal", "ts": int((now + 2) * 1e9), "data": later})
        assert recovered.signals["S3"]["p_end"] == .7
        assert recovered.signals["S3"]["execution_reason"] == "DAILY_OPPORTUNITY_CONSUMED"
        assert not recovered.signals["S3"]["triggered"]
        assert len(recovered.snapshot()["fills"]) == 1
        if rounding == "poly_us_order_half_even_v1":
            assert session.fee_accumulators
    finally:
        session.dispose()
        if recovered:
            recovered.dispose()


@pytest.mark.parametrize("state,reason", [
    ({"attempt": 1, "orders": ["unknown"]}, "ORDER_RECONCILIATION_REQUIRED"),
    ({"triggered": True}, "DAILY_OPPORTUNITY_CONSUMED"),
])
def test_account_block_does_not_freeze_weather(state, reason):
    from nice_weather.trading.engine import Session
    from nice_weather.trading.worker import run_config

    now, contracts, weather, _ = scenario()
    session = Session(run_config("test", "sandbox", "S3") | {
        "venue": "kalshi", "station_id": "KNYC", "execution_version": 3,
        "signal_version": "knyc-executable-v2",
    })
    try:
        for c in contracts:
            session.apply({"kind": "contract", "ts": int((now - 10) * 1e9), "data": c})
        session.strategy_state[f"{weather['day']}:S3"] = state
        session.apply({"kind": "weather_signal", "ts": int(now * 1e9), "data": weather})
        assert session.signals["S3"]["triggered"]
        assert session.signals["S3"]["execution_reason"] == reason
        stages = {e["stage"] for e in session.signals["S3"]["events"]}
        assert {"weather_trigger", "execution_rejected"} <= stages
        later = weather | {"p_end": .5, "received_at": now + 1, "data_cutoff": now + 1}
        session.apply({"kind": "weather_signal", "ts": int((now + 1) * 1e9), "data": later})
        assert session.signals["S3"]["p_end"] == .5
        assert not session.snapshot()["fills"]
    finally:
        session.dispose()


def test_signal_journal_deduplicates_commit_and_restart(tmp_path):
    import json

    from nice_weather.trading.recovery import PaperRunner
    from nice_weather.trading.storage import Results
    from nice_weather.trading.worker import run_config

    now, contracts, weather, _ = scenario()
    results = Results(tmp_path / "results.sqlite3")
    config = run_config("test", "sandbox", "S3") | {
        "venue": "kalshi", "station_id": "KNYC", "execution_version": 3,
        "signal_version": "knyc-executable-v2",
    }
    results.create("signals", "test", "sandbox", config)
    runner = PaperRunner(results, results.run("signals"))
    try:
        for i, c in enumerate(contracts):
            runner.apply(f"contract-{i}", {"kind": "contract", "ts": int((now - 10) * 1e9),
                                           "data": c})
        runner.apply("weather", {"kind": "weather_signal", "ts": int(now * 1e9), "data": weather})
        rows = results.inputs("signals")
        batches = [r for r in rows if json.loads(r["body"])["kind"] == "signal-events"]
        assert len(batches) == 1
        runner.commit()
        assert len(results.inputs("signals")) == len(rows)
    finally:
        runner.session.dispose()
    restored = PaperRunner(results, results.run("signals"))
    try:
        assert len(results.inputs("signals")) == len(rows)
        assert restored.session.signals["S3"]["triggered"]
        restored.apply("weather-later", {"kind": "weather_signal", "ts": int((now + 1) * 1e9),
                                         "data": weather | {"p_end": .5}})
        assert len(results.inputs("signals")) == len(rows) + 1
    finally:
        restored.session.dispose()

import copy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from test_knyc_terminal import scenario
from test_signals_v2 import limits

from nice_weather.trading.signal_research import research_signals, summarize
from nice_weather.trading.signals_v2 import evaluate


def historical():
    now, contracts, weather, books = scenario()
    weather.update(source_time=weather["received_at"], received_at=None,
                   observation_at=weather["observation_received_at"], observation_received_at=None,
                   model_data_cutoff=now - 86400, model_trained_at=now + 86400,
                   validation={"production_validated": False})
    for row in contracts:
        row.update(source_time=row["received_at"], received_at=None)
    for book in books.values():
        book.update(source_time=now - 3600, received_at=None, valid_until=now + 60)
    return now, contracts, weather, books


def test_source_research_preserves_missing_receipts_and_same_business_selection():
    now, contracts, weather, books = historical()
    original = copy.deepcopy((contracts, weather, books))
    for strategy in ("S1", "S2", "S3"):
        signal = evaluate(strategy, contracts, weather, books, now, limits(contracts),
                          context="historical_source")
        assert signal["action"] == "buy"
        assert signal["received_at"] is None
        assert all(leg["quote_received_at"] is None for leg in signal["legs"])
        assert signal["model_validation"] == {"production_validated": False}
        assert {e["stage"] for e in signal["events"]} >= {"weather_trigger", "candidate"}
    assert (contracts, weather, books) == original
    assert evaluate("S1", contracts, weather, books, now, limits(contracts))["action"] == "no-trade"


@pytest.mark.parametrize("change,reason", [
    ("weather", "FUTURE_INPUT"), ("model", "FUTURE_INPUT"),
    ("contract", "CONTRACT_MODEL_MISMATCH"), ("book", "FUTURE_QUOTE"),
    ("gap", "HISTORICAL_QUOTE_GAP"),
])
def test_history_does_not_admit_future_or_unbounded_quotes(change, reason):
    now, contracts, weather, books = historical()
    if change == "weather":
        weather["source_time"] = now + 1
    elif change == "model":
        weather["model_data_cutoff"] = now + 1
    elif change == "contract":
        contracts[0]["source_time"] = now + 1
    elif change == "book":
        books["test-1"]["source_time"] = now + 1
    else:
        books["test-1"]["valid_until"] = now
    signal = evaluate("S3", contracts, weather, books, now, limits(contracts),
                      context="historical_source")
    assert signal["reason"] == reason
    assert signal["legs"] == []


def test_bounded_approximation_is_a_market_price_not_probability():
    now, contracts, weather, _ = historical()
    book = {"price": .4, "price_source": "historical_ask", "max_quantity": 2,
            "source_time": now - 600, "received_at": None, "valid_until": now + 1}
    signal = evaluate("S3", contracts, weather, {"test-1": book}, now, limits(contracts),
                      context="historical_source")
    assert signal["legs"][0]["price"] == .4
    assert signal["legs"][0]["probability"] == .92
    book.pop("max_quantity")
    assert evaluate("S3", contracts, weather, {"test-1": book}, now, limits(contracts),
                    context="historical_source")["reason"] == "MISSING_QUANTITY_BOUND"


@pytest.mark.parametrize("day", ["2026-03-08", "2026-11-01"])
@pytest.mark.parametrize("clock,trigger", [("11:59:59", False), ("12:00:00", True),
                                         ("22:00:00", True), ("22:00:01", False)])
def test_new_york_window_at_seconds_and_dst(day, clock, trigger):
    _, contracts, weather, _ = scenario()
    now = datetime.fromisoformat(day + "T" + clock).replace(
        tzinfo=ZoneInfo("America/New_York")).timestamp()
    weather.update(day=day, received_at=now, data_cutoff=now, model_trained_at=now - 86400,
                   p_end=.9)
    for c in contracts:
        c.update(local_day=day, received_at=now - 1)
    signal = evaluate("S3", contracts, weather, {}, now, limits(contracts))
    assert signal["triggered"] == trigger


def test_weather_trigger_survives_missing_contract_and_quote_inputs():
    now, contracts, weather, books = scenario()
    missing = evaluate("S1", [], weather, {}, now, None)
    assert missing["triggered"] and missing["reason"] == "CONTRACT_HISTORY_MISSING"
    contracts[0]["parse_status"] = "ambiguous"
    signal = evaluate("S3", contracts, weather, books, now, limits(contracts))
    assert signal["triggered"] and signal["reason"] == "CONTRACT_RULES_UNVERIFIED"
    weather["p_end"] = .899999
    assert not evaluate("S3", contracts, weather, books, now, limits(contracts))["triggered"]


def test_invalid_probability_is_a_serializable_rejection():
    import json

    now, contracts, weather, books = scenario()
    weather["p_end"] = float("nan")
    result = evaluate("S1", contracts, weather, books, now, limits(contracts))
    assert result["reason"] == "INVALID_WEATHER_INPUT"
    assert result["p_end"] is None
    json.dumps(result, allow_nan=False)


def test_research_event_contract_and_counts():
    now, contracts, weather, books = historical()
    row = dict(asof=now, contracts=contracts, weather=weather, books=books, risk=limits(contracts))
    signals = list(research_signals([row]))
    summary = summarize(signals)
    assert summary["kalshi:S1"]["counts"]["candidate"] == 1
    assert summary["kalshi:S1"]["counts"]["fill"] == 0
    s1 = signals[0]
    assert len(s1["legs"]) == 2 and len({leg["token"] for leg in s1["legs"]}) == 2
    assert len({event["event_id"] for event in s1["events"]}) == len(s1["events"])
    with pytest.raises(ValueError, match="OUT_OF_ORDER"):
        list(research_signals([row, row | {"asof": now - 1}]))


def test_execution_feedback_partial_unknown_and_zero_fill():
    from nice_weather.trading.signals_v2 import opportunity_status

    state = {"attempt": 1, "orders": ["a"]}
    assert opportunity_status(state, [{"status": "UNKNOWN"}]) == "ORDER_RECONCILIATION_REQUIRED"
    assert opportunity_status(state, [{"status": "CANCELED", "filled": 0}]) is None
    partial = [{"status": "PARTIALLY_FILLED", "filled": .1}]
    assert opportunity_status(state, partial) == "DAILY_OPPORTUNITY_CONSUMED"
    assert opportunity_status(state, partial) == "DAILY_OPPORTUNITY_CONSUMED"
    assert opportunity_status(state, [{"status": "CANCELED", "filled": 0}]) == (
        "DAILY_OPPORTUNITY_CONSUMED"
    )


def test_model_generation_time_does_not_replace_research_training_cutoff():
    from nice_weather.trading.knyc_model import FEATURES, StrategyModel

    now, contracts, _, _ = historical()
    for c in contracts:
        c["settlement_source"] = "nws_cli"
    model = StrategyModel.__new__(StrategyModel)
    leaf = {"left": [-1], "right": [-1], "feature": [-2], "threshold": [-2], "value": [[1, 99]]}
    model.model = {
        "trained_at": now + 86400, "data_cutoff": now - 86400, "version": "knyc-test",
        "settlement_sources": ["nws_cli"], "medians": [0] * len(FEATURES),
        "end": {"classes": [0, 1], "trees": [leaf]}, "end_temperature": 1,
        "residual": {"classes": [0, 1], "trees": [leaf]}, "temperature": 1,
        "support": [0, 1], "prior": [.5, .5], "validation": {"production_validated": False},
    }
    values = dict.fromkeys(FEATURES, 0) | {"observed_proxy_high": 69}
    result = model.predict_features(values, contracts, now, context="historical_source")
    assert result["p_end"] == .99
    assert result["received_at"] is None and result["model_data_cutoff"] < now
    with pytest.raises(ValueError, match="MODEL_CUTOFF"):
        model.predict_features(values, contracts, now)
    model.model["data_cutoff"] = now + 1
    with pytest.raises(ValueError, match="MODEL_CUTOFF"):
        model.predict_features(values, contracts, now, context="historical_source")

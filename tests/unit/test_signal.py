from __future__ import annotations

from datetime import UTC, datetime

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.contract import parse_gamma_contract
from nice_weather.decision import build_outcomes, depth_quote
from nice_weather.domain import (
    BinProbability,
    HealthLevel,
    OrderBook,
    PriceLevel,
    ProbabilityEstimate,
)


def test_buy_uses_ask_depth_and_cost_buffers(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    item = contract.bins[5]
    now = datetime(2026, 8, 23, 4, 43, tzinfo=UTC)
    book = OrderBook(
        "book",
        "hash",
        item.yes_token_id,
        item.condition_id,
        now,
        now,
        (PriceLevel(0.38, 100),),
        (PriceLevel(0.40, 5), PriceLevel(0.42, 20)),
    )
    probabilities = tuple(
        BinProbability(bin_item.bin_id, 0.8 if bin_item.bin_id == item.bin_id else 0.02)
        for bin_item in contract.bins
    )
    estimate = ProbabilityEstimate(
        "test",
        now,
        80,
        None,
        80,
        80,
        76,
        84,
        probabilities,
        1.0,
        (),
    )
    outcomes = build_outcomes(
        "decision",
        contract,
        estimate,
        {item.yes_token_id: book},
        config,
        HealthLevel.OK,
        100,
        0,
    )
    selected = next(outcome for outcome in outcomes if outcome.bin_id == item.bin_id)

    assert selected.best_ask == 0.40
    assert selected.executable_price > selected.best_ask
    assert selected.executable_quantity * selected.executable_price <= 5.0 + 1e-9
    assert selected.fee > 0
    assert selected.risk_approved
    assert depth_quote(book, "buy", 10).quantity == 10


def test_blocked_health_stops_before_market_and_risk_checks(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    estimate = ProbabilityEstimate(
        "test",
        bundle.decision_time,
        80,
        None,
        80,
        80,
        76,
        84,
        tuple(BinProbability(item.bin_id, 1 / len(contract.bins)) for item in contract.bins),
        1.0,
        (),
    )
    outcomes = build_outcomes(
        "blocked",
        contract,
        estimate,
        bundle.books,
        config,
        HealthLevel.BLOCKED,
        0,
        config.paper.max_city_day_notional,
    )

    assert all(outcome.reason_codes == ("DATA_STALE",) for outcome in outcomes)

from __future__ import annotations

from datetime import UTC, datetime

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.contract import parse_gamma_contract
from nice_weather.domain import DecisionOutcome, OrderBook, PriceLevel, SignalAction
from nice_weather.paper import PaperBroker


def test_partial_fill_and_repeated_book_are_idempotent(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    item = parse_gamma_contract(bundle.gamma_snapshot.payload, config).bins[5]
    now = datetime(2026, 8, 23, 4, 43, tzinfo=UTC)
    outcome = DecisionOutcome(
        "decision",
        item.bin_id,
        item.label,
        0.8,
        0.39,
        0.4,
        0.395,
        10,
        0.4,
        5,
        0.4,
        0.01,
        0,
        0.02,
        0.37,
        SignalAction.BUY_YES,
        True,
    )
    book = OrderBook(
        "snapshot-1",
        "hash-1",
        item.yes_token_id,
        item.condition_id,
        now,
        now,
        (PriceLevel(0.39, 10),),
        (PriceLevel(0.4, 5),),
    )
    broker = PaperBroker(100)
    order = broker.submit(outcome, item, book, now, 2)

    assert order.status.value == "partially_filled"
    assert order.filled_quantity == 5
    broker.rematch(order, item, book, now)
    assert order.filled_quantity == 5
    assert len(broker.fills) == 1

    second_book = OrderBook(
        "snapshot-2",
        "hash-2",
        item.yes_token_id,
        item.condition_id,
        now,
        now,
        (PriceLevel(0.39, 10),),
        (PriceLevel(0.4, 5),),
    )
    broker.rematch(order, item, second_book, now)
    assert order.status.value == "filled"
    assert order.filled_quantity == 10
    assert len(broker.fills) == 2


def test_scenario_pnl_marks_each_final_bin(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    broker = PaperBroker(100)
    snapshot = broker.account_snapshot(contract.bins, bundle.books)
    assert set(snapshot.scenario_pnl) == {item.bin_id for item in contract.bins}
    assert all(value == 0 for value in snapshot.scenario_pnl.values())


def test_order_can_be_canceled_and_rejected(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    item = parse_gamma_contract(bundle.gamma_snapshot.payload, config).bins[5]
    now = datetime(2026, 8, 23, 4, 43, tzinfo=UTC)
    rejected_outcome = DecisionOutcome(
        "decision-rejected",
        item.bin_id,
        item.label,
        0.1,
        0.1,
        0.2,
        0.15,
        2,
        0.2,
        2,
        -0.1,
        0,
        0,
        0.02,
        -0.12,
        SignalAction.NO_TRADE,
        False,
    )
    book = bundle.books[item.yes_token_id]
    broker = PaperBroker(100)
    rejected = broker.submit(rejected_outcome, item, book, now, 2)
    assert rejected.status.value == "rejected"

    accepted_outcome = DecisionOutcome(
        "decision-cancel",
        item.bin_id,
        item.label,
        0.8,
        0.1,
        0.2,
        0.15,
        10,
        0.001,
        10,
        0.6,
        0,
        0,
        0.02,
        0.58,
        SignalAction.BUY_YES,
        True,
    )
    no_fill_book = type(book)(
        "cancel-snapshot",
        "cancel-hash",
        book.token_id,
        book.market_id,
        book.exchange_time,
        book.received_at,
        book.bids,
        book.asks,
    )
    accepted = broker.submit(accepted_outcome, item, no_fill_book, now, 2)
    assert accepted.status.value == "accepted"
    broker.cancel(accepted, now)
    assert accepted.status.value == "canceled"
    assert accepted.reserved_cash == 0


def test_exit_uses_bid_and_realizes_pnl(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    item = parse_gamma_contract(bundle.gamma_snapshot.payload, config).bins[5]
    now = datetime(2026, 8, 23, 4, 43, tzinfo=UTC)
    buy_book = type(bundle.books[item.yes_token_id])(
        "buy-snapshot",
        "buy-hash",
        item.yes_token_id,
        item.condition_id,
        now,
        now,
        (PriceLevel(0.39, 20),),
        (PriceLevel(0.4, 20),),
    )
    buy = DecisionOutcome(
        "buy-decision",
        item.bin_id,
        item.label,
        0.8,
        0.39,
        0.4,
        0.395,
        5,
        0.4,
        20,
        0.4,
        0.01,
        0,
        0.02,
        0.37,
        SignalAction.BUY_YES,
        True,
    )
    broker = PaperBroker(100)
    assert broker.submit(buy, item, buy_book, now, 2).status.value == "filled"

    sell_book = type(buy_book)(
        "sell-snapshot",
        "sell-hash",
        item.yes_token_id,
        item.condition_id,
        now,
        now,
        (PriceLevel(0.6, 20),),
        (PriceLevel(0.61, 20),),
    )
    sell = DecisionOutcome(
        "sell-decision",
        item.bin_id,
        item.label,
        0.2,
        0.6,
        0.61,
        0.605,
        5,
        0.6,
        20,
        0.4,
        0.01,
        0,
        0.02,
        0.37,
        SignalAction.EXIT_YES,
        True,
    )
    order = broker.submit(sell, item, sell_book, now, 2)

    assert order.side == "sell"
    assert order.status.value == "filled"
    assert broker.positions[item.bin_id].quantity == 0
    assert broker.realized_pnl > 0

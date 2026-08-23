from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.domain import RawSnapshot, RunMode, stable_id
from nice_weather.queries import DashboardQuery
from nice_weather.reason_codes import ReasonCode
from nice_weather.runner import _persist_market_block, _run_bundle, run_fixture_once
from nice_weather.store import WeatherStore


def test_forecast_coverage_gap_persists_no_trade(fixture_manifest, tmp_path) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    incomplete = replace(bundle, forecasts=bundle.forecasts[:-1])
    database = tmp_path / "coverage.sqlite3"

    decision = _run_bundle(incomplete, database, config, RunMode.FIXTURE)

    assert decision.overall_action == "NO_TRADE"
    assert ReasonCode.DATA_FORECAST_COVERAGE_GAP in decision.reason_codes
    summary = DashboardQuery(database).get_latest_decision_summary()
    assert summary is not None
    assert summary["overall_action"] == "NO_TRADE"


def test_empty_book_persists_no_trade(fixture_manifest, tmp_path) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    no_books = replace(bundle, books={})
    database = tmp_path / "empty-book.sqlite3"

    decision = _run_bundle(no_books, database, config, RunMode.FIXTURE)

    assert decision.overall_action == "NO_TRADE"
    assert ReasonCode.DATA_ORDER_BOOK_MISSING in decision.reason_codes


def test_wal_reader_sees_last_complete_decision_during_write(fixture_manifest, tmp_path) -> None:
    database = tmp_path / "concurrency.sqlite3"
    decision = run_fixture_once(fixture_manifest, database)
    query = DashboardQuery(database)

    with WeatherStore(database) as writer, writer.transaction() as connection:
        connection.execute(
            "UPDATE decisions SET status='in_progress' WHERE decision_id=?", (decision.decision_id,)
        )
        summary = query.get_latest_decision_summary()

    assert summary is not None
    assert summary["decision_id"] == decision.decision_id


def test_market_not_found_is_persisted_as_no_trade(tmp_path) -> None:
    config = load_city_config()
    received_at = datetime(2026, 8, 23, 5, 0, tzinfo=UTC)
    snapshot = RawSnapshot(
        stable_id("snapshot", "no-market"),
        "polymarket_gamma",
        "event",
        received_at,
        "empty",
        {"events": []},
    )
    database = tmp_path / "no-market.sqlite3"

    decision = _persist_market_block(
        snapshot, database, config, RunMode.SHADOW, ReasonCode.MARKET_NOT_FOUND
    )

    assert decision.overall_action == "NO_TRADE"
    assert decision.reason_codes == (ReasonCode.MARKET_NOT_FOUND,)
    assert DashboardQuery(database).get_outcome_snapshot(decision.decision_id) == []

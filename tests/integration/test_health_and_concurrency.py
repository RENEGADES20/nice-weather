from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta

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


def test_short_transaction_roles_share_wal_without_lock_errors(tmp_path) -> None:
    database = tmp_path / "role-soak.sqlite3"
    started_at = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    with WeatherStore(database) as store:
        store.init_schema()

    def collector_writer() -> None:
        with WeatherStore(database) as store:
            for index in range(40):
                timestamp = started_at + timedelta(milliseconds=index)
                store.record_poll_attempt(
                    source="nws",
                    kind="station_observations",
                    station_id="KLGA",
                    requested_at=timestamp,
                    received_at=timestamp,
                    http_status=200,
                    succeeded=True,
                    content_changed=False,
                )

    def runner_writer() -> None:
        with WeatherStore(database) as store:
            for index in range(40):
                store.record_system_event(
                    started_at + timedelta(milliseconds=index),
                    "INFO",
                    "runner",
                    "soak",
                    f"cycle-{index}",
                )

    def r2_writer() -> None:
        with WeatherStore(database) as store:
            for index in range(40):
                timestamp = started_at + timedelta(milliseconds=index)
                store.record_r2_export(
                    export_id=stable_id("r2", index),
                    export_type="raw",
                    source="nws",
                    local_date="2026-09-01",
                    object_key=f"soak/{index}.ndjson.gz",
                    sha256=f"{index:064x}",
                    size_bytes=index,
                    source_ids=[],
                    created_at=timestamp,
                    uploaded_at=timestamp,
                    status="uploaded",
                )

    def dashboard_reader() -> None:
        query = DashboardQuery(database)
        for _ in range(80):
            query.list_decisions()

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(collector_writer),
            executor.submit(runner_writer),
            executor.submit(r2_writer),
            executor.submit(dashboard_reader),
        ]
        for future in futures:
            future.result(timeout=15)

    with WeatherStore(database, read_only=True) as store:
        counts = store.table_counts()
    assert counts["poll_attempts"] == 40
    assert counts["system_events"] == 40
    assert counts["r2_exports"] == 40


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

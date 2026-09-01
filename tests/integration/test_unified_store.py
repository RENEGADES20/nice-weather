import gzip
import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import nice_weather.runner as runner_module
from nice_weather.adapters.fixture import load_fixture
from nice_weather.cli import _clone_migrate
from nice_weather.config import load_city_config
from nice_weather.domain import RunMode, SourceCapture, content_hash, stable_id
from nice_weather.runner import _run_bundle, _run_live_cycle
from nice_weather.store import WeatherStore
from nice_weather.weather_repository import (
    CapturedWeatherState,
    OfflineWeatherRepository,
    WeatherRepository,
)


def _capture(payload: object, received_at: datetime) -> SourceCapture:
    digest = content_hash(payload)
    return SourceCapture(
        capture_id=stable_id("capture", digest),
        source="nws",
        kind="station_observations",
        station_id="KLGA",
        requested_at=received_at,
        received_at=received_at,
        local_date=date(2026, 8, 27),
        source_version=digest,
        content_hash=digest,
        request_url="https://api.weather.gov/stations/KLGA/observations",
        http_status=200,
        content_type="application/json",
        raw_bytes=gzip.compress(json.dumps(payload).encode()),
    )


def test_weather_repository_selects_latest_revision_available_as_of(tmp_path) -> None:
    database = tmp_path / "weather.sqlite3"
    observed_at = datetime(2026, 8, 27, 11, 0, tzinfo=UTC)
    zone = ZoneInfo("America/New_York")
    first_received = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    second_received = datetime(2026, 8, 27, 13, 0, tzinfo=UTC)
    row = {
        "observed_at": observed_at,
        "temperature_c": 20.0,
        "temperature_f": 68.0,
        "raw_unit": "unit:degC",
        "raw_text": "first",
        "quality_control": {},
        "zone": zone,
    }
    with WeatherStore(database) as store:
        store.init_schema()
        store.save_source_capture(
            _capture({"v": 1}, first_received), payload={"v": 1}, observations=[row]
        )
        store.save_source_capture(
            _capture({"v": 2}, second_received),
            payload={"v": 2},
            observations=[{**row, "temperature_f": 69.0, "raw_text": "revised"}],
        )

    repository = WeatherRepository(database)
    early = repository.get_state_as_of(
        "KLGA", date(2026, 8, 27), datetime(2026, 8, 27, 12, 30, tzinfo=UTC)
    )
    late = repository.get_state_as_of(
        "KLGA", date(2026, 8, 27), datetime(2026, 8, 27, 13, 30, tzinfo=UTC)
    )
    assert early.observations[0].temperature_f == 68.0
    assert late.observations[0].temperature_f == 69.0


def test_clone_migrate_preserves_weather_and_initializes_unified_tables(tmp_path) -> None:
    source = tmp_path / "weather.sqlite3"
    target = tmp_path / "nice-weather.sqlite3"
    with WeatherStore(source) as store:
        store.init_schema()
        store.save_source_capture(
            _capture({"v": 1}, datetime(2026, 8, 27, 12, 0, tzinfo=UTC)),
            payload={"v": 1},
        )
    result = _clone_migrate(source, target)
    assert result["ok"]
    with WeatherStore(target, read_only=True) as store:
        assert store.table_counts()["source_captures"] == 1
        assert store.table_counts()["execution_quotes"] == 0
        assert store.table_counts()["paper_fills"] == 0


def test_shadow_persists_minimal_quotes_without_full_book_levels(
    fixture_manifest, tmp_path
) -> None:
    database = tmp_path / "shadow.sqlite3"
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    _run_bundle(bundle, database, config, RunMode.SHADOW)
    with WeatherStore(database, read_only=True) as store:
        counts = store.table_counts()
        assert counts["execution_quotes"] > 0
        assert counts["order_book_levels"] == 0
        assert counts["weather_observations"] == 0
        assert counts["forecast_points"] == 0
        assert counts["weather_feature_snapshots"] == 1


def test_live_cycle_freezes_decision_time_after_quote_receipt(
    fixture_manifest, tmp_path, monkeypatch
) -> None:
    config = load_city_config()
    fixture = load_fixture(fixture_manifest, config)
    weather_as_of = fixture.decision_time
    quote_received_at = weather_as_of + timedelta(milliseconds=500)
    final_decision_time = weather_as_of + timedelta(seconds=1)
    clock = iter((weather_as_of - timedelta(seconds=1), weather_as_of, final_decision_time))

    class FakeMarketAdapter:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def discover(self, _config, _request_time):
            return fixture.gamma_snapshot

        def fetch_candidate_quotes(self, token_ids, requested_at):
            assert requested_at == weather_as_of
            return [
                replace(
                    snapshot,
                    requested_at=requested_at,
                    received_at=quote_received_at,
                )
                for snapshot in fixture.snapshots
                if snapshot.source == "polymarket_clob" and snapshot.token_id in token_ids
            ]

    class FakeWeatherRepository:
        def __init__(self, _database_path) -> None:
            pass

        def get_state_as_of(self, station_id, local_date, decision_time):
            assert decision_time == weather_as_of
            return CapturedWeatherState(
                station_id=station_id,
                local_date=local_date,
                decision_time=decision_time,
                observations=fixture.observations,
                forecasts=fixture.forecasts,
                settlement=None,
                input_capture_ids=(),
            )

    monkeypatch.setattr(runner_module, "PolymarketReadOnlyAdapter", FakeMarketAdapter)
    monkeypatch.setattr(runner_module, "WeatherRepository", FakeWeatherRepository)
    monkeypatch.setattr(runner_module, "utc_now", lambda: next(clock))

    database = tmp_path / "live-as-of.sqlite3"
    decision = _run_live_cycle(RunMode.SHADOW, database)

    assert decision.decision_time == final_decision_time
    with WeatherStore(database, read_only=True) as store:
        assert store.table_counts()["decisions"] == 1
        latest_quote = store.connection.execute(
            "SELECT MAX(received_at) FROM execution_quotes"
        ).fetchone()[0]
    assert datetime.fromisoformat(latest_quote) == quote_received_at
    assert quote_received_at <= decision.decision_time


def test_offline_repository_accepts_v1_and_v2_manifests() -> None:
    assert OfflineWeatherRepository.normalize_manifest('{"schema_version":1}')[
        "schema_version"
    ] == 1
    assert OfflineWeatherRepository.normalize_manifest('{"schema_version":2}')[
        "schema_version"
    ] == 2

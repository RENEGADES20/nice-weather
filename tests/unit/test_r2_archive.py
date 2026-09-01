import gzip
import io
import json
from datetime import UTC, datetime

from nice_weather.config import load_city_config
from nice_weather.domain import SourceCapture, content_hash, stable_id
from nice_weather.r2_archive import WEATHER_EXPORT_TABLES, R2Archive, R2Config
from nice_weather.store import WeatherStore


class FakeBody:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, **kwargs: object) -> None:
        self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))] = bytes(kwargs["Body"])

    def get_object(self, **kwargs: object) -> dict[str, FakeBody]:
        payload = self.objects[(str(kwargs["Bucket"]), str(kwargs["Key"]))]
        return {"Body": FakeBody(payload)}


def _r2() -> R2Config:
    return R2Config(
        endpoint_url="https://example.r2.cloudflarestorage.com",
        bucket="weather",
        access_key_id="test-access",
        secret_access_key="test-secret",
    )


def test_r2_allowlist_contains_weather_only() -> None:
    forbidden = {"decisions", "decision_outcomes", "execution_quotes", "paper_fills"}
    assert forbidden.isdisjoint(WEATHER_EXPORT_TABLES)
    assert {"poll_attempts", "settlement_rows"}.issubset(WEATHER_EXPORT_TABLES)


def test_raw_sync_is_content_addressed_and_idempotent(tmp_path) -> None:
    database = tmp_path / "weather.sqlite3"
    payload = {"features": [{"temperature": 20.0}]}
    digest = content_hash(payload)
    now = datetime(2026, 8, 27, 12, 7, tzinfo=UTC)
    capture = SourceCapture(
        capture_id=stable_id("capture", digest),
        source="nws",
        kind="station_observations",
        station_id="KLGA",
        requested_at=now,
        received_at=now,
        local_date=now.date(),
        source_version=digest,
        content_hash=digest,
        request_url="https://api.weather.gov/stations/KLGA/observations",
        http_status=200,
        content_type="application/json",
        raw_bytes=gzip.compress(json.dumps(payload).encode()),
    )
    with WeatherStore(database) as store:
        store.init_schema()
        assert store.save_source_capture(capture, payload=payload)

    fake = FakeS3()
    archive = R2Archive(database, load_city_config(), _r2(), client=fake)
    keys = archive.sync_raw()
    assert len(keys) == 1
    assert "/raw/source=nws/local_date=2026-08-27/" in keys[0]
    assert archive.sync_raw() == []
    with WeatherStore(database) as store:
        assert store.r2_usage_summary()["object_count"] == 1


def test_r2_check_writes_and_reads_without_deleting(tmp_path) -> None:
    database = tmp_path / "weather.sqlite3"
    fake = FakeS3()
    archive = R2Archive(database, load_city_config(), _r2(), client=fake)
    result = archive.check()
    assert result["ok"]
    assert len(fake.objects) == 1
    assert "/healthchecks/" in result["object_key"]


def test_parquet_round_trip_when_pyarrow_available(tmp_path) -> None:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return
    database = tmp_path / "weather.sqlite3"
    payload = {"value": 1}
    digest = content_hash(payload)
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    capture = SourceCapture(
        capture_id=stable_id("capture", digest),
        source="nws",
        kind="hourly_forecast",
        station_id="KLGA",
        requested_at=now,
        received_at=now,
        local_date=now.date(),
        source_version=digest,
        content_hash=digest,
        request_url="https://api.weather.gov/gridpoints/OKX/forecast/hourly",
        http_status=200,
        content_type="application/json",
        raw_bytes=gzip.compress(json.dumps(payload).encode()),
        issued_at=now,
    )
    with WeatherStore(database) as store:
        store.init_schema()
        store.save_source_capture(capture, payload=payload, forecast_periods=[])
    fake = FakeS3()
    archive = R2Archive(database, load_city_config(), _r2(), client=fake)
    keys = archive.export_parquet(now.date())
    forecast_key = next(key for key in keys if "table=weather_forecasts" in key)
    table = pq.read_table(io.BytesIO(fake.objects[("weather", forecast_key)]))
    assert table.num_rows == 1

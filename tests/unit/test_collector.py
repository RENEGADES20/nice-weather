import gzip
import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from nice_weather.collector import next_settlement_due, parse_settlement_page
from nice_weather.config import load_city_config
from nice_weather.domain import SourceCapture, content_hash, stable_id
from nice_weather.store import WeatherStore


def test_settlement_page_parses_tmax_and_next_day_row() -> None:
    text = """
    KLGA Hourly Data
    Max Temperature: 86 F
    2026-08-26 23:51 84 F
    2026-08-27 00:51 82 F
    """
    parsed = parse_settlement_page(text, date(2026, 8, 26), ZoneInfo("America/New_York"))
    assert parsed.parse_status == "parsed"
    assert parsed.tmax_f == 86.0
    assert parsed.finalized
    assert parsed.first_next_day_temperature_f == 82.0
    assert parsed.first_next_day_observed_at == datetime(2026, 8, 27, 4, 51, tzinfo=UTC)


def test_settlement_page_uses_rows_and_blocks_ambiguous_content() -> None:
    rows = parse_settlement_page(
        "Hourly Data\n08/26/2026 12:00 80 F\n08/26/2026 15:00 83 F",
        date(2026, 8, 26),
        ZoneInfo("America/New_York"),
    )
    assert rows.tmax_f == 83.0
    assert not rows.finalized

    ambiguous = parse_settlement_page(
        "KLGA page temporarily unavailable",
        date(2026, 8, 26),
        ZoneInfo("America/New_York"),
    )
    assert ambiguous.parse_status == "ambiguous"
    assert ambiguous.no_trade_reason == "SETTLEMENT_PAGE_UNPARSEABLE"


def test_settlement_page_parses_live_weather_gov_display_rows() -> None:
    text = """
    Weather conditions for: New York, La Guardia Airport, NY
    Date/Time (L) Temp. (°F) Dew Point (°F)
    Aug 27, 11:51 am\t77\t69\t77
    Aug 27, 10:51 am\t77\t70\t79
    Aug 27, 4:51 pm\t82\t60\t47
    Aug 26, 11:51 pm\t76\t67\t74
    """
    parsed = parse_settlement_page(text, date(2026, 8, 27), ZoneInfo("America/New_York"))
    assert parsed.parse_status == "parsed"
    assert parsed.tmax_f == 82.0
    assert not parsed.finalized

    finalized = parse_settlement_page(text, date(2026, 8, 26), ZoneInfo("America/New_York"))
    assert finalized.tmax_f == 76.0
    assert finalized.finalized
    assert finalized.first_next_day_temperature_f == 77.0
    assert finalized.first_next_day_observed_at == datetime(2026, 8, 27, 14, 51, tzinfo=UTC)


def test_settlement_schedule_uses_close_window_and_hour_five() -> None:
    config = load_city_config()
    close_time = datetime(2026, 8, 27, 3, 55, tzinfo=UTC)  # 23:55 EDT
    assert (next_settlement_due(close_time, config) - close_time).total_seconds() == 120

    regular = datetime(2026, 8, 27, 14, 22, tzinfo=UTC)  # 10:22 EDT
    due = next_settlement_due(regular, config).astimezone(config.zone)
    assert (due.hour, due.minute) == (11, 5)

    before_close = datetime(2026, 8, 28, 3, 5, tzinfo=UTC)  # 23:05 EDT
    close_due = next_settlement_due(before_close, config).astimezone(config.zone)
    assert (close_due.hour, close_due.minute) == (23, 50)


def _capture(payload: object, received_at: datetime) -> SourceCapture:
    digest = content_hash(payload)
    return SourceCapture(
        capture_id=stable_id("capture", digest),
        source="nws",
        kind="station_observations",
        station_id="KLGA",
        requested_at=received_at,
        received_at=received_at,
        local_date=received_at.date(),
        source_version=digest,
        content_hash=digest,
        request_url="https://api.weather.gov/stations/KLGA/observations",
        http_status=200,
        content_type="application/json",
        raw_bytes=gzip.compress(json.dumps(payload).encode()),
        observed_at=received_at,
    )


def test_capture_dedup_and_observation_revisions(tmp_path) -> None:
    database = tmp_path / "collector.sqlite3"
    observed_at = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    zone = ZoneInfo("America/New_York")
    first = _capture({"temperature": 20.0}, observed_at)
    second = _capture({"temperature": 20.1}, observed_at.replace(minute=5))
    row = {
        "observed_at": observed_at,
        "temperature_c": 20.0,
        "temperature_f": 68.0,
        "raw_unit": "unit:degC",
        "raw_text": "",
        "quality_control": {"temperature": "V"},
        "zone": zone,
    }
    with WeatherStore(database) as store:
        store.init_schema()
        assert store.save_source_capture(first, payload={"temperature": 20.0}, observations=[row])
        assert not store.save_source_capture(
            first, payload={"temperature": 20.0}, observations=[row]
        )
        revised_row = {**row, "temperature_c": 20.1, "temperature_f": 68.18}
        assert store.save_source_capture(
            second, payload={"temperature": 20.1}, observations=[revised_row]
        )
        revisions = store.connection.execute(
            "SELECT revision FROM weather_observations ORDER BY revision"
        ).fetchall()
        assert [item[0] for item in revisions] == [1, 2]
        assert store.connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 3

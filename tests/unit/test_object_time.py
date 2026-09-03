from datetime import UTC, date, datetime

from nice_weather.migrations import _migration_v6
from nice_weather.queries import object_day_bounds
from nice_weather.store import WeatherStore


def test_new_york_object_day_bounds_cover_dst_lengths() -> None:
    spring_start, spring_end = object_day_bounds(
        date(2026, 3, 8), 1, "America/New_York"
    )
    fall_start, fall_end = object_day_bounds(date(2026, 11, 1), 1, "America/New_York")

    assert (spring_end - spring_start).total_seconds() == 23 * 3600
    assert (fall_end - fall_start).total_seconds() == 25 * 3600


def test_object_day_bounds_ignore_process_display_timezone() -> None:
    chicago = object_day_bounds(date(2026, 9, 3), 2, "America/New_York")
    new_york = object_day_bounds(date(2026, 9, 3), 2, "America/New_York")

    assert chicago == new_york


def test_migration_recomputes_object_dates_from_new_york_time(tmp_path) -> None:
    database = tmp_path / "object-dates.sqlite3"
    with WeatherStore(database) as store:
        store.init_schema()
        received = datetime(2026, 9, 4, 0, 30, tzinfo=UTC).isoformat()
        store.connection.execute(
            """
            INSERT INTO source_captures(
              capture_id,source,kind,station_id,requested_at,received_at,local_date,
              source_version,content_hash,request_url,http_status,content_type,
              content_encoding,raw_size_bytes,raw_blob
            ) VALUES('midnight','test','weather','KLGA',?,?,?, 'v','h','https://example.test',
                     200,'application/json','identity',0,X'')
            """,
            (received, received, "2026-09-04"),
        )
        store.connection.execute(
            """
            INSERT INTO weather_forecasts(
              forecast_id,capture_id,source,station_id,issued_at,received_at,local_date,
              source_version,content_hash,period_count
            ) VALUES('forecast','midnight','nws','KLGA',?,?,?,'v','h2',0)
            """,
            (received, received, "2026-09-04"),
        )

        _migration_v6(store.connection)

        capture_date = store.connection.execute(
            "SELECT local_date,object_local_date FROM source_captures "
            "WHERE capture_id='midnight'"
        ).fetchone()
        forecast_date = store.connection.execute(
            "SELECT local_date,object_local_date FROM weather_forecasts "
            "WHERE forecast_id='forecast'"
        ).fetchone()
        assert tuple(capture_date) == ("2026-09-03", "2026-09-03")
        assert tuple(forecast_date) == ("2026-09-03", "2026-09-03")

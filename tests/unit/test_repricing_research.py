from __future__ import annotations

import gzip
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from nice_weather.config import load_city_config
from nice_weather.domain import SettlementEvidence, SourceCapture, content_hash, stable_id
from nice_weather.market_stream import MarketStreamCollector, TokenMetadata, _event_time
from nice_weather.queries import DashboardQuery
from nice_weather.research import _persistent_threshold, repair_settlement_dates
from nice_weather.store import WeatherStore


def test_weather_queries_use_instants_for_offset_timestamps_and_dst(tmp_path) -> None:
    zone = ZoneInfo("America/New_York")
    for target in (date(2026, 9, 5), date(2026, 3, 8), date(2026, 11, 1)):
        start = datetime.combine(target, datetime.min.time(), zone)
        end = start + timedelta(days=1)
        received = (end + timedelta(days=2)).astimezone(UTC)
        database = tmp_path / f"offset-{target}.sqlite3"
        # Mixed source offsets, both repeated fall-back hours, and excluded next midnight.
        times = [
            start - timedelta(hours=2),
            start,
            start + timedelta(hours=1),
            (start + timedelta(hours=1)).replace(fold=1),
            (end - timedelta(hours=1)).astimezone(UTC),
            end,
        ]
        times = list(dict.fromkeys(item.isoformat() for item in times))
        rows = [(datetime.fromisoformat(stamp), float(index)) for index, stamp in enumerate(times)]
        expected = [
            value
            for stamp, value in rows
            if start.timestamp() <= stamp.timestamp() < end.timestamp()
        ]
        with WeatherStore(database) as store:
            store.init_schema()
            capture = replace(_settlement_capture(str(target), target, received), source="nws")
            store.save_source_capture(
                capture,
                payload={},
                observations=[
                    {"observed_at": stamp, "temperature_f": value, "zone": zone}
                    for stamp, value in rows
                ],
                forecast_periods=[
                    {"valid_at": stamp, "temperature_f": value} for stamp, value in rows
                ],
            )
            store.save_settlement_evidence(
                SettlementEvidence(
                    evidence_id=str(target),
                    capture_id=capture.capture_id,
                    station_id="KLGA",
                    local_date=target,
                    received_at=received,
                    table_text="offset fixture",
                    parse_status="parsed",
                ),
                tuple(rows),
            )
        query = DashboardQuery(database)
        timeline = query.get_weather_timeline(target, 1, received, zone.key)
        history = query.get_repricing_weather_history(target, 1, received, zone.key, 0)
        for key in ("observations", "forecasts", "running_tmax"):
            assert [row["temperature_f"] for row in timeline[key]] == expected
        for key in ("observations", "settlement_rows"):
            assert [row["temperature_f"] for row in history[key]] == expected
        before = query.get_weather_timeline(
            target, 1, received - timedelta(microseconds=1), zone.key
        )
        assert all(not values for values in before.values())


def _settlement_capture(identity: str, local_day: date, received_at: datetime) -> SourceCapture:
    digest = content_hash({"identity": identity})
    return SourceCapture(
        capture_id=stable_id("capture", identity),
        source="weather_gov",
        kind="settlement_page",
        station_id="KLGA",
        requested_at=received_at - timedelta(seconds=1),
        received_at=received_at,
        local_date=local_day,
        source_version=digest,
        content_hash=digest,
        request_url="https://www.weather.gov/wrh/timeseries?site=KLGA",
        http_status=200,
        content_type="text/html",
        raw_bytes=gzip.compress(identity.encode()),
    )


def test_settlement_repair_uses_visible_history_when_latest_page_drops_rows(tmp_path) -> None:
    database = tmp_path / "repair.sqlite3"
    target = date(2026, 9, 1)
    first_received = datetime(2026, 9, 2, 3, 30, tzinfo=UTC)
    final_received = datetime(2026, 9, 2, 5, 30, tzinfo=UTC)
    with WeatherStore(database) as store:
        store.init_schema()
        first = _settlement_capture("first", target, first_received)
        store.save_source_capture(first, payload={"capture": "first"})
        store.save_settlement_evidence(
            SettlementEvidence(
                evidence_id="evidence-first",
                capture_id=first.capture_id,
                station_id="KLGA",
                local_date=target,
                received_at=first_received,
                table_text="Sep 1 11:00 pm 80",
                parse_status="parsed",
                tmax_f=80,
            ),
            ((datetime(2026, 9, 2, 3, 0, tzinfo=UTC), 80.0),),
        )
        final = _settlement_capture("final", target, final_received)
        store.save_source_capture(final, payload={"capture": "final"})
        store.save_settlement_evidence(
            SettlementEvidence(
                evidence_id="evidence-final",
                capture_id=final.capture_id,
                station_id="KLGA",
                local_date=target,
                received_at=final_received,
                table_text="Sep 2 1:00 am 70",
                parse_status="parsed",
                tmax_f=70,
                finalized=True,
            ),
            ((datetime(2026, 9, 2, 5, 0, tzinfo=UTC), 70.0),),
        )

    dry_run = repair_settlement_dates(database, load_city_config(), apply=False)
    assert dry_run["evidence_changes"] == 2
    repair_settlement_dates(database, load_city_config(), apply=True)

    with WeatherStore(database, read_only=True) as store:
        final = store.connection.execute(
            "SELECT * FROM settlement_evidence WHERE evidence_id='evidence-final'"
        ).fetchone()
        label = store.connection.execute("SELECT * FROM weather_daily_labels").fetchone()
    assert final["tmax_f"] == 80.0
    assert final["object_local_date"] == "2026-09-01"
    assert label["official_tmax_f"] == 80.0
    assert label["label_version"] == "weather-gov-hourly-v3"


def test_settlement_repair_rebuilds_rows_from_immutable_capture(tmp_path) -> None:
    database = tmp_path / "raw-repair.sqlite3"
    target = date(2026, 9, 1)
    received = datetime(2026, 9, 2, 5, 30, tzinfo=UTC)
    raw = "Hourly Data\n09/01/2026 16:00 85 F\n09/02/2026 01:00 70 F"
    capture = SourceCapture(
        capture_id="raw-capture",
        source="weather_gov",
        kind="settlement_page",
        station_id="KLGA",
        requested_at=received - timedelta(seconds=1),
        received_at=received,
        local_date=target,
        source_version="raw-v1",
        content_hash=content_hash(raw),
        request_url="https://www.weather.gov/wrh/timeseries?site=KLGA",
        http_status=200,
        content_type="text/html",
        raw_bytes=gzip.compress(raw.encode()),
    )
    evidence = SettlementEvidence(
        evidence_id="raw-evidence",
        capture_id=capture.capture_id,
        station_id="KLGA",
        local_date=target,
        received_at=received,
        table_text=raw,
        parse_status="parsed",
        tmax_f=70,
        finalized=True,
    )
    with WeatherStore(database) as store:
        store.init_schema()
        store.save_source_capture(capture, payload={})
        store.save_settlement_evidence(evidence, ())

    dry_run = repair_settlement_dates(database, load_city_config(), apply=False)
    assert dry_run["raw_parse_errors"] == 0
    assert dry_run["reconstructed_rows"] == 2
    assert dry_run["missing_rows"] == 2
    repair_settlement_dates(database, load_city_config(), apply=True)

    with WeatherStore(database, read_only=True) as store:
        stored = store.connection.execute(
            "SELECT tmax_f FROM settlement_evidence WHERE evidence_id='raw-evidence'"
        ).fetchone()
        label = store.connection.execute("SELECT * FROM weather_daily_labels").fetchone()
        rows = store.connection.execute(
            "SELECT object_local_date FROM settlement_rows ORDER BY observed_at"
        ).fetchall()
    assert stored["tmax_f"] == 85
    assert label["official_tmax_f"] == 85
    assert [row["object_local_date"] for row in rows] == ["2026-09-01", "2026-09-02"]


def test_dashboard_repricing_reads_raw_hourly_temp_and_resets_main_tmax_by_day(
    tmp_path,
) -> None:
    database = tmp_path / "dashboard-hourly.sqlite3"
    target = date(2026, 9, 1)
    received = datetime(2026, 9, 1, 3, tzinfo=UTC)
    capture = _settlement_capture("dashboard-hourly", target, received)
    with WeatherStore(database) as store:
        store.init_schema()
        store.save_source_capture(capture, payload={})
        store.save_settlement_evidence(
            SettlementEvidence(
                evidence_id="dashboard-hourly-evidence",
                capture_id=capture.capture_id,
                station_id="KLGA",
                local_date=target,
                received_at=received,
                table_text="Hourly fixture",
                parse_status="parsed",
                tmax_f=80,
                object_timezone="America/New_York",
            ),
            (
                (datetime(2026, 9, 1, 12, tzinfo=UTC), 80.0),
                (datetime(2026, 9, 2, 12, tzinfo=UTC), 70.0),
            ),
        )

    query = DashboardQuery(database)
    history = query.get_repricing_weather_history(
        target,
        2,
        datetime(2026, 9, 3, tzinfo=UTC),
        "America/New_York",
        observation_age_seconds=5_400,
    )
    timeline = query.get_weather_timeline(
        target, 2, datetime(2026, 9, 3, tzinfo=UTC), "America/New_York"
    )

    assert [row["temperature_f"] for row in history["settlement_rows"]] == [80, 70]
    assert [row["temperature_f"] for row in timeline["running_tmax"]] == [80, 70]
    assert [row["object_local_date"] for row in timeline["running_tmax"]] == [
        "2026-09-01",
        "2026-09-02",
    ]


def test_market_ticks_keep_a_b_a_and_drop_identical_repeat(tmp_path) -> None:
    database = tmp_path / "market.sqlite3"
    config = load_city_config()
    with WeatherStore(database) as store:
        store.init_schema()
    collector = MarketStreamCollector(config, str(database))
    metadata = {"token": TokenMetadata("event", "condition", "market", "bin", "token", "80 F")}
    messages = [
        '{"event_type":"price_change","timestamp":"1788364800000",'
        '"price_changes":[{"asset_id":"token","best_bid":"0.2","best_ask":"0.4"}]}',
        '{"event_type":"price_change","timestamp":"1788364830000",'
        '"price_changes":[{"asset_id":"token","best_bid":"0.3","best_ask":"0.5"}]}',
        '{"event_type":"price_change","timestamp":"1788364860000",'
        '"price_changes":[{"asset_id":"token","best_bid":"0.2","best_ask":"0.4"}]}',
    ]
    assert [collector.process_message(message, metadata) for message in messages] == [1, 1, 1]
    assert collector.process_message(messages[-1], metadata) == 0
    with WeatherStore(database, read_only=True) as store:
        rows = store.connection.execute(
            "SELECT mid FROM market_top_ticks ORDER BY exchange_event_at"
        ).fetchall()
    assert [round(row["mid"], 3) for row in rows] == [0.3, 0.4, 0.3]


def test_market_event_time_accepts_seconds_and_milliseconds() -> None:
    fallback = datetime(2026, 9, 3, tzinfo=UTC)
    expected = datetime(2026, 9, 3, tzinfo=UTC)
    assert _event_time(str(round(expected.timestamp())), fallback) == expected
    assert _event_time(str(round(expected.timestamp() * 1000)), fallback) == expected


def test_market_stream_ignores_heartbeat_messages(tmp_path) -> None:
    collector = MarketStreamCollector(load_city_config(), str(tmp_path / "market.sqlite3"))
    assert collector.process_message("PONG", {}) == 0
    assert collector.process_message(b"  ", {}) == 0


def test_price_in_threshold_requires_sixty_seconds_of_persistence() -> None:
    start = datetime(2026, 9, 3, 12, tzinfo=UTC)
    ticks = [
        {"time": start, "mid": 0.91},
        {"time": start + timedelta(seconds=30), "mid": 0.89},
        {"time": start + timedelta(seconds=60), "mid": 0.92},
        {"time": start + timedelta(seconds=121), "mid": 0.93},
    ]
    assert _persistent_threshold(ticks, 0.9, start) == start + timedelta(seconds=60)


def test_market_cursor_uses_receipt_order_for_late_event_ticks(tmp_path) -> None:
    database = tmp_path / "late-tick.sqlite3"
    config = load_city_config()
    with WeatherStore(database) as store:
        store.init_schema()
    collector = MarketStreamCollector(config, str(database))
    target = TokenMetadata("event", "condition", "market", "bin", "token", "80 F")
    exchange_time = datetime(2026, 9, 3, 12, tzinfo=UTC)
    collector._save(
        target,
        exchange_event_at=exchange_time,
        received_at=exchange_time + timedelta(seconds=2),
        source="clob_ws",
        status="available",
        changes={"best_bid": 0.4, "best_ask": 0.5},
        raw_event={"sequence": 1},
    )
    query = DashboardQuery(database)
    initial = query.get_market_bin_history(
        "event", ["bin"], exchange_time - timedelta(minutes=1), exchange_time + timedelta(minutes=1)
    )
    collector._save(
        target,
        exchange_event_at=exchange_time - timedelta(seconds=1),
        received_at=exchange_time + timedelta(seconds=3),
        source="clob_ws",
        status="available",
        changes={"best_bid": 0.45, "best_ask": 0.5},
        raw_event={"sequence": 2},
    )

    incremental = query.get_market_bin_history(
        "event",
        ["bin"],
        exchange_time - timedelta(minutes=1),
        exchange_time + timedelta(minutes=1),
        initial["cursor"],
    )

    assert [item["best_bid"] for item in incremental["ticks"]] == [0.45]


def test_market_history_paginates_past_twenty_thousand_rows(tmp_path, monkeypatch) -> None:
    query = DashboardQuery(tmp_path / "unused.sqlite3")
    received = "2026-09-03T12:00:00+00:00"
    pages = [
        [{"tick_id": f"tick-{index:05d}", "received_at": received} for index in range(20_000)],
        [{"tick_id": "tick-20000", "received_at": received}],
    ]

    def fake_query(_sql, _parameters=()):
        return pages.pop(0) if pages else []

    monkeypatch.setattr(query, "_query", fake_query)
    result = query.get_market_bin_history(
        "event",
        ["bin"],
        datetime(2026, 9, 3, 11, tzinfo=UTC),
        datetime(2026, 9, 3, 13, tzinfo=UTC),
    )

    assert len(result["ticks"]) == 20_001
    assert result["cursor"] == f"{received}|tick-20000"

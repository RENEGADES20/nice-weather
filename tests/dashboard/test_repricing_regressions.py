from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

import nice_weather.dashboard as dashboard
from nice_weather.config import load_city_config
from nice_weather.market_stream import MarketStreamCollector, TokenMetadata
from nice_weather.queries import DashboardQuery, object_day_bounds
from nice_weather.store import WeatherStore


def tick(at, identifier="q", kind="quote", **changes):
    return {
        "tick_id": identifier,
        "bin_id": "bin",
        "source": "clob_ws",
        "event_kind": kind,
        "status": "available",
        "exchange_event_at": at.isoformat(),
        "received_at": at.isoformat(),
        "best_bid": 0.04,
        "best_ask": 0.06,
        "mid": 0.05,
        "last_trade_price": None,
        **changes,
    }


def test_source_isolation_and_same_price_trades(tmp_path):
    database = tmp_path / "ticks.sqlite3"
    collector = MarketStreamCollector(load_city_config(), str(database))
    metadata = TokenMetadata("event", "condition", "market", "bin", "token", "80 F")
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)

    def save(index, source, kind, changes):
        return collector._save(
            metadata,
            exchange_event_at=now + timedelta(seconds=index),
            received_at=now + timedelta(seconds=index),
            source=source,
            status="available",
            changes=changes,
            raw_event={"id": index},
            event_kind=kind,
        )

    assert save(0, "clob_ws", "quote", {"best_bid": 0.04, "best_ask": 0.06})
    assert save(1, "gamma_fallback", "gamma", {"best_bid": 0.001, "best_ask": 0.003})
    assert save(2, "clob_ws", "trade", {"last_trade_price": 0.004})
    assert save(3, "clob_ws", "trade", {"last_trade_price": 0.004})
    assert not save(3, "clob_ws", "trade", {"last_trade_price": 0.004})
    assert save(4, "clob_ws", "quote", {"best_bid": 0.05})
    with WeatherStore(database, read_only=True) as store:
        rows = [
            dict(row)
            for row in store.connection.execute(
                "SELECT * FROM market_top_ticks ORDER BY received_at"
            )
        ]
        assert store.verify_schema()["schema_version"] == 7
    assert [row["mid"] for row in rows] == [0.05, 0.002, None, None, 0.055]
    assert rows[-1]["last_trade_price"] is None
    assert dashboard._select_price(rows, now + timedelta(seconds=3))["value"] == 0.05


def test_quote_expiry_trade_clock_and_legacy():
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)
    quote = tick(now)
    trade = tick(
        now + timedelta(minutes=9),
        "t",
        "trade",
        last_trade_price=0.07,
        mid=None,
        best_bid=None,
        best_ask=None,
    )
    assert dashboard._select_price([quote, trade], now + timedelta(minutes=10))["value"] == 0.05
    assert dashboard._select_price([quote, trade], now + timedelta(minutes=11))["value"] == 0.07
    assert dashboard._select_price([quote, trade], now + timedelta(minutes=15)) is None
    legacy = tick(now, event_kind=None, mid=None, last_trade_price=0.08)
    assert dashboard._select_price([legacy], now) is None
    zero = tick(now, mid=0, best_bid=0, best_ask=0)
    assert dashboard._select_price([zero], now)["display_value"] == 0
    assert dashboard._price_display_value(45) is None
    assert dashboard._price_display_value(1) == 100
    disconnected = tick(now + timedelta(seconds=1), "d", "disconnect", status="disconnect")
    assert dashboard._select_price([quote, disconnected], now + timedelta(seconds=2)) is None


def test_price_main_uses_known_time_and_expires_without_ticks():
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)
    quote = tick(now, received_at=(now + timedelta(seconds=30)).isoformat())
    points = dashboard._price_points(
        [quote], "bin", now, now + timedelta(hours=1), now + timedelta(minutes=11)
    )
    assert points[0]["value"] is None
    assert next(p for p in points if p["value"] is not None)["time"] == now.timestamp() + 30
    assert points[-1]["value"] is None
    history = {"observations": [], "forecasts": [], "settlement_rows": []}
    values = dashboard._repricing_difference_inputs(
        history,
        [quote],
        now,
        now + timedelta(hours=1),
        now + timedelta(minutes=11),
        "bin",
        5400,
        21600,
    )[-1]["points"]
    assert values[0]["value"] is None
    assert values[1]["value"] == 5
    with pytest.raises(ValueError, match="unexpected bin"):
        dashboard._repricing_difference_inputs(
            history,
            [quote],
            now,
            now + timedelta(hours=1),
            now + timedelta(minutes=1),
            "other",
            5400,
            21600,
        )


def test_single_day_dst_and_future_snapshot():
    assert (
        object_day_bounds(date(2026, 3, 8), 1, "America/New_York")[1]
        - object_day_bounds(date(2026, 3, 8), 1, "America/New_York")[0]
    ).total_seconds() == 23 * 3600
    start, end = object_day_bounds(date(2026, 11, 1), 1, "America/New_York")
    assert (end - start).total_seconds() == 25 * 3600
    now = start - timedelta(hours=12)
    rows = [
        {
            "capture_id": "forecast",
            "forecast_point_id": str(i),
            "received_at": now.isoformat(),
            "issued_at": now.isoformat(),
            "valid_at": t.isoformat(),
            "temperature_f": value,
        }
        for i, (t, value) in enumerate([(start, 70), (end, 80)])
    ]
    price = dashboard._select_price([tick(now)], now)
    inputs = dashboard._future_inputs({"forecasts": rows}, price, start, end, now, 21600, "bin")
    assert len(inputs[0]["points"]) == 1500
    assert inputs[0]["points"][0]["value"] == 70
    assert inputs[-1]["points"][0]["value"] == 5
    assert inputs[1]["points"] == inputs[2]["points"] == []
    assert rows[0]["received_at"] == now.isoformat()


def test_incremental_inputs_equal_full_reconstruction(monkeypatch):
    start = datetime(2026, 9, 5, 4, tzinfo=UTC)
    clock = [start + timedelta(minutes=2, seconds=30)]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz or UTC)

    monkeypatch.setattr(dashboard, "datetime", Clock)
    monkeypatch.setattr(dashboard.st, "session_state", {})
    rows = [tick(start + timedelta(seconds=10))]
    history = {"observations": [], "settlement_rows": [], "forecasts": []}
    weather_reads = []

    class Query:
        def __init__(self, path):
            pass

        def repricing_weather_version(self):
            return (0, 0, 0)

        def get_repricing_weather_history(self, *args):
            weather_reads.append(1)
            return history

        def get_weather_timeline(self, *args):
            return {"observations": [], "forecasts": [], "running_tmax": []}

        def get_forecast_revision_events(self, *args):
            return []

        def get_repricing_ticks(self, event, bin_id, begin, end, as_of, cursor):
            new = [row for row in rows if cursor is None or row["received_at"] > cursor]
            return {"ticks": new, "cursor": new[-1]["received_at"] if new else cursor}

    monkeypatch.setattr(dashboard, "DashboardQuery", Query)

    def read():
        return dashboard._timeline_data(
            Path("unused"),
            "event",
            start.date(),
            "America/New_York",
            1,
            "bin",
            ["forecast", "metar"],
        )

    read()
    rows.append(
        tick(start + timedelta(minutes=3, seconds=1), "new", mid=0.1, best_bid=0.09, best_ask=0.11)
    )
    clock[0] += timedelta(minutes=2)
    _, incremental, _, _, end = read()
    full = dashboard._repricing_difference_inputs(
        history, rows, start, end, clock[0], "bin", 5400, 21600
    )
    assert incremental == full
    assert len(weather_reads) == 1
    clock[0] += timedelta(minutes=11)
    _, expired, _, _, _ = read()
    assert expired[-1]["points"][-1]["value"] is None


def test_receipt_cursor_includes_recovery_of_unchanged_old_book(tmp_path):
    database = tmp_path / "seed.sqlite3"
    collector = MarketStreamCollector(load_city_config(), str(database))
    now = datetime(2026, 9, 5, 4, tzinfo=UTC)
    metadata = TokenMetadata("event", "condition", "market", "bin", "token", "80 F")
    for seconds in [0, 300]:
        assert collector._save(
            metadata,
            exchange_event_at=now - timedelta(days=1),
            received_at=now + timedelta(seconds=seconds),
            source="clob_ws",
            status="reconnect_snapshot",
            changes={"best_bid": 0.04, "best_ask": 0.06},
            raw_event={"book": "same"},
            event_kind="snapshot",
        )
    query = DashboardQuery(database)
    first = query.get_repricing_ticks(
        "event", "bin", now - timedelta(minutes=10), now + timedelta(days=1), now
    )
    later = query.get_repricing_ticks(
        "event",
        "bin",
        now - timedelta(minutes=10),
        now + timedelta(days=1),
        now + timedelta(minutes=5),
        first["cursor"],
    )
    assert len(first["ticks"]) == len(later["ticks"]) == 1
    assert later["ticks"][0]["tick_id"] != first["ticks"][0]["tick_id"]


def test_v6_backup_migrates_without_rewriting_legacy_ticks(tmp_path, monkeypatch):
    import sqlite3

    import nice_weather.migrations as migrations

    old = tmp_path / "v6.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    with monkeypatch.context() as baseline:
        baseline.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:3])
        baseline.setattr(migrations, "LATEST_SCHEMA_VERSION", 6)
        with WeatherStore(old) as store:
            store.init_schema()
            store.connection.execute(
                "INSERT INTO market_top_ticks(tick_id,event_id,condition_id,market_id,bin_id,"
                "token_id,label,exchange_event_at,received_at,object_timezone,object_local_date,"
                "mid,source,status,event_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "legacy",
                    "event",
                    "condition",
                    "market",
                    "bin",
                    "token",
                    "80 F",
                    "2026-09-05T12:00:00+00:00",
                    "2026-09-05T12:00:00+00:00",
                    "America/New_York",
                    "2026-09-05",
                    0.05,
                    "clob_ws",
                    "available",
                    "hash",
                ),
            )
            store.connection.commit()
            with sqlite3.connect(backup) as copy:
                store.connection.backup(copy)
    with WeatherStore(old) as store:
        store.init_schema()
        assert store.verify_schema()["ok"]
        row = dict(store.connection.execute("SELECT * FROM market_top_ticks").fetchone())
        assert row["event_kind"] is None and row["mid"] == 0.05
        assert store.connection.execute("SELECT count(*) FROM market_top_ticks").fetchone()[0] == 1
    with sqlite3.connect(backup) as copy:
        assert copy.execute("SELECT version FROM schema_meta").fetchone()[0] == 6
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_new_forecast_missing_point_replaces_old_snapshot():
    now = datetime(2026, 9, 5, 12, tzinfo=UTC)
    rows = [
        {
            "capture_id": capture,
            "forecast_point_id": f"{capture}-{i}",
            "received_at": (now - timedelta(minutes=2 if capture == "old" else 1)).isoformat(),
            "issued_at": (now - timedelta(minutes=3)).isoformat(),
            "valid_at": (now + timedelta(hours=i)).isoformat(),
            "temperature_f": value,
        }
        for capture, values in [("old", [70, 72]), ("new", [70, None])]
        for i, value in enumerate(values)
    ]
    target = int((now + timedelta(minutes=30)).timestamp())
    result = dashboard._forecast_point(dashboard._forecast_snapshots(rows), target, 21600)
    assert result["value"] is None and result["reason"] == "missing-forecast"
    _, hashes = dashboard._series_delta(
        [{"id": "forecast", "points": [{"time": 1, "value": 70}, {"time": 2, "value": 72}]}], {}
    )
    delta, _ = dashboard._series_delta(
        [{"id": "forecast", "points": [{"time": 1, "value": 70}]}], hashes
    )
    assert delta[0]["removedTimes"] == [2.0]


def test_market_stream_keeps_wal_files_between_committed_ticks(tmp_path):
    collector = MarketStreamCollector(load_city_config(), str(tmp_path / "wal.sqlite3"))
    try:
        store = collector._storage()
        assert collector._storage() is store
        assert not store.connection.in_transaction
        with WeatherStore(collector.database_path, read_only=True) as reader:
            assert reader.connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 7
        assert Path(collector.database_path + "-wal").exists()
        assert Path(collector.database_path + "-shm").exists()
    finally:
        collector.close()

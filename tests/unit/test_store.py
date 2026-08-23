from datetime import UTC, datetime

from nice_weather.store import WeatherStore


def test_schema_initializes_with_wal(tmp_path) -> None:
    path = tmp_path / "weather.sqlite3"
    with WeatherStore(path) as store:
        store.init_schema()
        counts = store.table_counts()
        assert counts["schema_meta"] == 1
        assert counts["decisions"] == 0
        journal_mode = store.connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"

    with WeatherStore(path, read_only=True) as reader:
        assert reader.table_counts()["raw_snapshots"] == 0


def test_runner_lock_allows_only_one_owner(tmp_path) -> None:
    database = tmp_path / "lock.sqlite3"
    now = datetime(2026, 8, 23, 5, 0, tzinfo=UTC)
    with WeatherStore(database) as store:
        store.init_schema()
        assert store.acquire_runner_lock("writer", "owner-1", now, 120)
        assert not store.acquire_runner_lock("writer", "owner-2", now, 120)
        store.release_runner_lock("writer", "owner-1", now)
        assert store.acquire_runner_lock("writer", "owner-2", now, 120)

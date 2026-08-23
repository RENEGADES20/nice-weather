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


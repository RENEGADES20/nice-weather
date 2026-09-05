from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

from nice_weather.store import WeatherStore


def test_writer_waits_through_short_contention_without_losing_record(tmp_path):
    database = tmp_path / "contention.sqlite3"
    with WeatherStore(database) as store:
        store.init_schema()
    acquired = Event()

    def hold_writer():
        with WeatherStore(database) as store, store.transaction():
            acquired.set()
            Event().wait(5.3)

    received_at = datetime(2026, 9, 5, 12, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=1) as executor:
        holder = executor.submit(hold_writer)
        assert acquired.wait(10)
        with WeatherStore(database) as writer:
            writer.record_system_event(received_at, "INFO", "test", "received", "preserved")
            assert not writer.connection.in_transaction
        holder.result()
    with WeatherStore(database, read_only=True) as reader:
        row = reader.connection.execute("SELECT * FROM system_events").fetchone()
        assert row["occurred_at"] == received_at.isoformat()
        assert row["message"] == "preserved"

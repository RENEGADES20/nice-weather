import sqlite3
import threading
import time

from nice_weather.trading.history import PriceHistory, read_history


def test_incremental_history_and_nonblocking_cold_load(tmp_path, monkeypatch):
    path = tmp_path / "ticks.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE market_top_ticks (token_id,source,event_kind,received_at,mid)")
        con.executemany(
            "INSERT INTO market_top_ticks VALUES ('a','clob_ws','quote',?,?)",
            [("2026-09-13T12:00:01+00:00", 0.4), ("2026-09-13T12:00:02+00:00", 0.41)],
        )
    cursor, rows = read_history(path, "a")
    assert len(rows) == 1 and rows[0]["mid"] == 0.41
    with sqlite3.connect(path) as con:
        con.execute(
            "INSERT INTO market_top_ticks VALUES ('a','clob_ws','quote',?,?)",
            ("2026-09-13T12:01:01+00:00", 0.42),
        )
    end, new = read_history(path, "a", cursor)
    assert end > cursor and [r["mid"] for r in new] == [0.42]
    gate = threading.Event()

    def slow(*args):
        gate.wait(10)
        return read_history(*args)

    monkeypatch.setattr("nice_weather.trading.history.read_history", slow)
    cache = PriceHistory()
    start = time.monotonic()
    try:
        assert cache.get(path, "a")[2] is False
        assert time.monotonic() - start < 0.2
    finally:
        gate.set()
        cache.pool.shutdown()
    assert cache.get(path, "a")[2] is True

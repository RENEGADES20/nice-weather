import sqlite3
from contextlib import ExitStack, closing

import pytest

from nice_weather.store import WeatherStore
from nice_weather.trading.storage import connect


@pytest.mark.parametrize("kind", ["weather", "trading"])
def test_wal_releases_unused_space_without_truncating_reader_history(tmp_path, kind):
    path = tmp_path / "retention.sqlite3"
    with ExitStack() as stack:
        writer = (
            stack.enter_context(WeatherStore(path)).connection
            if kind == "weather"
            else stack.enter_context(connect(path))
        )
        writer.execute("CREATE TABLE evidence (body BLOB)")
        writer.commit()
        reader = stack.enter_context(closing(sqlite3.connect(path)))
        reader.execute("BEGIN")
        assert reader.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
        for _ in range(20):
            writer.execute("INSERT INTO evidence VALUES (zeroblob(1048576))")
            writer.commit()
        wal = path.with_name(path.name + "-wal")
        assert wal.stat().st_size > 16 * 1024**2
        # The pinned snapshot must still be readable despite exceeding the limit.
        assert reader.execute("SELECT count(*) FROM evidence").fetchone()[0] == 0
        reader.rollback()
        assert reader.execute("SELECT count(*) FROM evidence").fetchone()[0] == 20
        result = writer.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        assert result[0] == 0 and result[1] == result[2]
        # A normal write resets/reuses the fully checkpointed WAL; no forced truncation.
        writer.execute("INSERT INTO evidence VALUES (x'01')")
        writer.commit()
        assert wal.stat().st_size <= 16 * 1024**2
        assert writer.execute("SELECT sum(length(body)) FROM evidence").fetchone()[0] == (
            20 * 1024**2 + 1
        )
    with closing(sqlite3.connect(path)) as reopened:
        assert reopened.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert reopened.execute("SELECT count(*) FROM evidence").fetchone()[0] == 21

import sqlite3

import pytest

from nice_weather.trading.storage import Requests, connect


def test_idle_reader_sees_commits_without_holding_wal_snapshot(tmp_path):
    queue = Requests(tmp_path / "requests.sqlite3")
    with connect(queue.path, readonly=True) as reader:
        assert queue.pending("sandbox-test", "sandbox", connection=reader) == []
        queue.submit("first", "sandbox-test", "sandbox", "start", {})
        assert [r["request_id"] for r in queue.pending(
            "sandbox-test", "sandbox", connection=reader
        )] == ["first"]
        assert not reader.in_transaction
        queue.finish("first", "accepted")
        assert queue.pending("sandbox-test", "sandbox", connection=reader) == []
        with connect(queue.path) as writer:
            assert tuple(writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()) == (0, 0, 0)
        queue.submit("second", "sandbox-test", "sandbox", "stop", {})
        rows = queue.pending("sandbox-test", "sandbox", connection=reader)
        assert rows[0]["request_id"] == "second"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.execute("UPDATE requests SET status='accepted'")

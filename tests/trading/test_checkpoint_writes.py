import json
import sqlite3

import pytest
from test_native_workbench import event

from nice_weather.trading.recovery import PaperRunner
from nice_weather.trading.storage import Results, connect, digest
from nice_weather.trading.worker import run_config


def test_cursor_checkpoints_reuse_pages_and_restore_latest_cursor(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    # Approximate the observed 100–170 KiB checkpoints without deleting history.
    config = run_config("sandbox-test", "sandbox") | {
        "feed_cursor": 100000, "retained_evidence": "historical evidence " * 8000,
    }
    results.create("run", "sandbox-test", "sandbox", config)
    with connect(results.path) as keeper:
        runner = PaperRunner(results, results.run("run"))
        runner.apply("clock", event("clock", 1, {}))
        keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for cursor in range(100001, 100021):
            runner.session.config["feed_cursor"] = cursor
            runner.commit()
        written = results.path.with_name(results.path.name + "-wal").stat().st_size
        # Replacing the row writes several MiB. An unchanged overflow payload
        # must not be rewritten for every small cursor change.
        assert 0 < written < 512 * 1024
        saved = keeper.execute("SELECT body,checksum FROM paper_state").fetchone()
        assert digest(json.loads(saved["body"])) == saved["checksum"]
        runner.session.dispose()
        restored = PaperRunner(results, results.run("run"))
        assert restored.session.config["feed_cursor"] == 100020
        assert restored.session.config["retained_evidence"] == config["retained_evidence"]
        assert restored.session.snapshot()["cash"] == 100
        restored.session.dispose()


def test_failed_view_update_rolls_back_checkpoint_and_receipt(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", "sandbox-test", "sandbox", run_config("sandbox-test", "sandbox"))
    runner = PaperRunner(results, results.run("run"))
    with connect(results.path) as con:
        before = tuple(con.execute("SELECT * FROM paper_state").fetchone())
        con.execute("CREATE TRIGGER fail_view BEFORE UPDATE ON runs BEGIN "
                    "SELECT RAISE(ABORT,'injected failure'); END")
    runner.session.config["feed_cursor"] = 123
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        runner.commit("failed-input")
    runner.session.dispose()
    with connect(results.path) as con:
        assert tuple(con.execute("SELECT * FROM paper_state").fetchone()) == before
        assert not con.execute("SELECT 1 FROM paper_receipts WHERE input_id='failed-input'")\
            .fetchone()
        con.execute("DROP TRIGGER fail_view")
    restored = PaperRunner(results, results.run("run"))
    assert restored.session.config.get("feed_cursor") is None
    assert "failed-input" not in restored.seen
    restored.session.dispose()

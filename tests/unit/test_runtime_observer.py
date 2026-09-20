import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "runtime_observer", Path(__file__).resolve().parents[2] / "scripts/observe_knyc_runtime.py"
)
OBSERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBSERVER)


def test_observer_reads_queue_without_writing_or_exposing_account_config(tmp_path, monkeypatch):
    (tmp_path / "runtime-manifest.json").write_text(json.dumps({"commit": "abc"}))
    with sqlite3.connect(tmp_path / "feed.sqlite3") as con:
        con.executescript("CREATE TABLE feed_latest(kind,key,seq,received);"
                          "CREATE TABLE feed_events(seq,received);"
                          "INSERT INTO feed_events VALUES (2,900);")
    with sqlite3.connect(tmp_path / "results.sqlite3") as con:
        con.execute("CREATE TABLE runs(account,status,updated,config,mode)")
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?)",
                    ("sandbox-kalshi", "running", 1000,
                     json.dumps({"feed_cursor": 1, "cash": 100}), "sandbox"))
    monkeypatch.setattr(OBSERVER.time, "time", lambda: 1000)
    monkeypatch.setattr(OBSERVER.subprocess, "check_output",
                        lambda *a, **k: "MainPID=0\nActiveState=active\n")
    result = OBSERVER.sample(tmp_path, tmp_path)
    assert result["accounts"][0]["oldest_pending_age"] == 100
    assert result["sha"] == "abc" and "config" not in result["accounts"][0]
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        OBSERVER.query(tmp_path / "feed.sqlite3", "DELETE FROM feed_events")

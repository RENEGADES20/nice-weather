from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path


def encoded(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


@contextmanager
def connect(path: Path, *, readonly=False):
    if readonly:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        # This caps reusable space, never live WAL pages needed by a reader.
        con.execute("PRAGMA journal_size_limit=16777216")
        con.execute("PRAGMA synchronous=FULL")
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


class Requests:
    """Only this database is writable by the dashboard."""

    def __init__(self, path: Path):
        self.path = path
        with connect(path) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, account TEXT NOT NULL, mode TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                expires REAL NOT NULL, status TEXT NOT NULL DEFAULT 'queued', error TEXT)""")

    def submit(self, request_id, account, mode, kind, payload, *, ttl=60):
        if mode not in {"sandbox", "backtest", "live"} or not account or not request_id:
            raise ValueError("Invalid request identity")
        if not re.fullmatch(mode + r"-[A-Za-z0-9_-]{1,64}", account):
            raise ValueError("Account namespace must match execution mode")
        if not isinstance(payload, dict):
            raise ValueError("Request payload must be an object")
        if kind not in {
            "order",
            "cancel",
            "replace",
            "close",
            "start",
            "stop",
            "backtest",
            "cancel_run",
            "balance",
            "reset",
            "configure",
            "reconcile",
        }:
            raise ValueError("Unsupported request")
        if not 0 < ttl <= 86400:
            raise ValueError("Invalid request validity")
        body = encoded(payload)
        with connect(self.path) as con:
            con.execute(
                "INSERT OR IGNORE INTO requests VALUES (?,?,?,?,?,?,?,'queued',NULL)",
                (request_id, account, mode, kind, body, time.time(), time.time() + ttl),
            )
            row = con.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if (row["account"], row["mode"], row["kind"], row["payload"]) != (
                account,
                mode,
                kind,
                body,
            ):
                raise ValueError("Request ID reused with a different payload")
        return request_id

    def pending(self, account, mode, *, connection=None):
        with (nullcontext(connection) if connection is not None
              else connect(self.path, readonly=True)) as con:
            return [
                dict(row)
                for row in con.execute(
                    "SELECT * FROM requests WHERE account=? AND mode=? AND status='queued' "
                    "ORDER BY created,request_id",
                    (account, mode),
                )
            ]

    def finish(self, request_id, status, error=None):
        with connect(self.path) as con:
            con.execute(
                "UPDATE requests SET status=?,error=? WHERE request_id=?",
                (status, error, request_id),
            )


class Results:
    def __init__(self, path: Path):
        self.path = path
        with connect(path) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY, account TEXT NOT NULL, mode TEXT NOT NULL,
                    config TEXT NOT NULL, status TEXT NOT NULL, snapshot TEXT, error TEXT,
                    updated REAL NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS sandbox_account ON runs(account)
                    WHERE mode='sandbox';
                CREATE TABLE IF NOT EXISTS inputs (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, input_id TEXT NOT NULL,
                    body TEXT NOT NULL, snapshot_hash TEXT,
                    PRIMARY KEY(run_id,seq), UNIQUE(run_id,input_id));
                CREATE TABLE IF NOT EXISTS equity (
                    run_id TEXT NOT NULL, seq INTEGER NOT NULL, ts INTEGER NOT NULL,
                    equity REAL, drawdown REAL, PRIMARY KEY(run_id,seq));
            """)

    def create(self, run_id, account, mode, config):
        with connect(self.path) as con:
            con.execute(
                "INSERT INTO runs VALUES (?,?,?,?, 'starting', NULL,NULL,?)",
                (run_id, account, mode, encoded(config), time.time()),
            )

    def run(self, run_id=None, account=None):
        with connect(self.path, readonly=True) as con:
            row = con.execute(
                "SELECT * FROM runs WHERE run_id=?"
                if run_id
                else "SELECT * FROM runs WHERE account=? AND mode='sandbox'",
                (run_id or account,),
            ).fetchone()
            return dict(row) if row else None

    def append(self, run_id, input_id, event):
        # The commit precedes engine execution. An interrupted input is replayed exactly once.
        with connect(self.path) as con:
            prior = con.execute(
                "SELECT seq,body FROM inputs WHERE run_id=? AND input_id=?", (run_id, input_id)
            ).fetchone()
            if prior:
                if prior["body"] != encoded(event):
                    raise ValueError("Input identity collision")
                return None
            seq = con.execute(
                "SELECT COALESCE(MAX(seq),0)+1 FROM inputs WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            con.execute(
                "INSERT INTO inputs VALUES (?,?,?,?,NULL)", (run_id, seq, input_id, encoded(event))
            )
        return seq

    def checkpoint(self, run_id, seq, snapshot, status="running"):
        with connect(self.path) as con:
            con.execute(
                "UPDATE inputs SET snapshot_hash=? WHERE run_id=? AND seq=?",
                (digest(snapshot), run_id, seq),
            )
            con.execute(
                "INSERT OR REPLACE INTO equity VALUES (?,?,?,?,?)",
                (run_id, seq, snapshot["ts"], snapshot["equity"], snapshot["drawdown"]),
            )
            con.execute(
                "UPDATE runs SET status=?,snapshot=?,updated=?,error=NULL WHERE run_id=?",
                (status, encoded(snapshot), time.time(), run_id),
            )

    def status(self, run_id, status, error=None):
        with connect(self.path) as con:
            con.execute(
                "UPDATE runs SET status=?,error=?,updated=? WHERE run_id=?",
                (status, error, time.time(), run_id),
            )

    def inputs(self, run_id):
        with connect(self.path, readonly=True) as con:
            return [
                dict(r)
                for r in con.execute("SELECT * FROM inputs WHERE run_id=? ORDER BY seq", (run_id,))
            ]


@contextmanager
def single_writer(path: Path):
    """OS lock is released on process death; no stale PID lease or manual reset."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)

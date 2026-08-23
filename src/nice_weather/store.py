from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any


class WeatherStore:
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path).resolve()
        self.read_only = read_only
        if read_only:
            uri = f"file:{self.path.as_posix()}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.path, timeout=5.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> WeatherStore:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def init_schema(self) -> None:
        if self.read_only:
            raise RuntimeError("Cannot initialize schema through a read-only connection")
        schema = files("nice_weather").joinpath("schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
        self.connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            raise RuntimeError("Cannot start a write transaction through a read-only connection")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def table_counts(self) -> dict[str, int]:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {
            row["name"]: int(
                self.connection.execute(f'SELECT COUNT(*) FROM "{row["name"]}"').fetchone()[0]
            )
            for row in rows
        }

    def latest_complete_decision(self) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM decisions
            WHERE status = 'complete'
            ORDER BY decision_time DESC, decision_id DESC
            LIMIT 1
            """
        ).fetchone()

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


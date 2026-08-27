from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any

from nice_weather.domain import (
    Decision,
    MarketContract,
    PaperOrderStatus,
    ProbabilityEstimate,
    RawSnapshot,
    SettlementEvidence,
    SourceCapture,
    UnifiedState,
    content_hash,
    stable_id,
)
from nice_weather.paper import (
    PaperAccountSnapshot,
    PaperBroker,
    PaperFill,
    PaperOrder,
    Position,
)
from nice_weather.reason_codes import ReasonCode


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
        self._migrate_v3_columns()
        self.connection.commit()

    def _migrate_v3_columns(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(weather_observations)")
        }
        additions = {
            "source": "TEXT NOT NULL DEFAULT 'aviationweather'",
            "temperature_c": "REAL",
            "raw_unit": "TEXT",
            "quality_control_json": "TEXT NOT NULL DEFAULT '{}'",
            "source_version": "TEXT",
            "revision": "INTEGER NOT NULL DEFAULT 1",
            "local_date": "TEXT",
        }
        for name, definition in additions.items():
            if name not in columns:
                self.connection.execute(
                    f'ALTER TABLE weather_observations ADD COLUMN "{name}" {definition}'
                )

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

    def load_paper_broker(self, starting_cash: float) -> PaperBroker:
        account = self.connection.execute(
            """
            SELECT * FROM paper_accounts
            ORDER BY created_at DESC, decision_id DESC LIMIT 1
            """
        ).fetchone()
        if account is None:
            return PaperBroker(starting_cash)
        broker = PaperBroker(
            starting_cash=starting_cash,
            cash=float(account["cash"]),
            realized_pnl=float(account["realized_pnl"]),
        )
        for bin_id, data in json.loads(account["positions_json"]).items():
            if float(data["quantity"]):
                broker.positions[bin_id] = Position(
                    quantity=float(data["quantity"]), cost_basis=float(data["cost_basis"])
                )
        for row in self.connection.execute("SELECT * FROM paper_orders ORDER BY created_at"):
            broker.orders.append(
                PaperOrder(
                    order_id=row["order_id"],
                    decision_id=row["decision_id"],
                    bin_id=row["bin_id"],
                    side=row["side"],
                    limit_price=float(row["limit_price"]),
                    quantity=float(row["quantity"]),
                    filled_quantity=float(row["filled_quantity"]),
                    average_fill_price=float(row["average_fill_price"]),
                    reserved_cash=float(row["reserved_cash"]),
                    status=PaperOrderStatus(row["status"]),
                    created_at=datetime.fromisoformat(row["created_at"]),
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                    stale_after_cycle=int(row["stale_after_cycle"]),
                    reason_codes=tuple(
                        ReasonCode(code) for code in json.loads(row["reason_codes_json"])
                    ),
                )
            )
        for row in self.connection.execute("SELECT * FROM paper_fills ORDER BY filled_at"):
            fill = PaperFill(
                fill_id=row["fill_id"],
                order_id=row["order_id"],
                decision_id=row["decision_id"],
                bin_id=row["bin_id"],
                book_snapshot_id=row["book_snapshot_id"],
                book_hash=row["book_hash"],
                side=row["side"],
                price=float(row["price"]),
                quantity=float(row["quantity"]),
                fee=float(row["fee"]),
                filled_at=datetime.fromisoformat(row["filled_at"]),
                level_index=int(row["level_index"]),
            )
            broker.fills.append(fill)
            broker._fill_keys.add((fill.order_id, fill.book_hash, fill.side, fill.level_index))
        return broker

    def acquire_runner_lock(
        self, lock_name: str, owner_id: str, acquired_at: datetime, ttl_seconds: int
    ) -> bool:
        expires_at = acquired_at + timedelta(seconds=ttl_seconds)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT owner_id, expires_at FROM runner_locks WHERE lock_name=?", (lock_name,)
            ).fetchone()
            if (
                row is not None
                and row["owner_id"] != owner_id
                and datetime.fromisoformat(row["expires_at"]) > acquired_at
            ):
                return False
            connection.execute(
                """
                INSERT OR REPLACE INTO runner_locks VALUES(?,?,?,?)
                """,
                (lock_name, owner_id, self._iso(acquired_at), self._iso(expires_at)),
            )
        return True

    def release_runner_lock(self, lock_name: str, owner_id: str, released_at: datetime) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                UPDATE runner_locks SET expires_at=?
                WHERE lock_name=? AND owner_id=?
                """,
                (self._iso(released_at), lock_name, owner_id),
            )

    def record_system_event(
        self,
        occurred_at: datetime,
        level: str,
        source: str,
        event_type: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO system_events VALUES(?,?,?,?,?,?,?)",
                (
                    stable_id("system_event", occurred_at, source, event_type, message),
                    self._iso(occurred_at),
                    level,
                    source,
                    event_type,
                    message,
                    self.dumps(context or {}),
                ),
            )

    def save_source_capture(
        self,
        capture: SourceCapture,
        *,
        payload: Any,
        observations: list[dict[str, Any]] | None = None,
        forecast_periods: list[dict[str, Any]] | None = None,
    ) -> bool:
        """Persist one changed source response and its normalized rows atomically."""
        with self.transaction() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO source_captures(
                  capture_id, source, kind, station_id, requested_at, source_time,
                  observed_at, issued_at, received_at, local_date, source_version,
                  content_hash, request_url, http_status, content_type, content_encoding,
                  raw_size_bytes, raw_blob
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    capture.capture_id,
                    capture.source,
                    capture.kind,
                    capture.station_id,
                    self._iso(capture.requested_at),
                    self._iso(capture.source_time),
                    self._iso(capture.observed_at),
                    self._iso(capture.issued_at),
                    self._iso(capture.received_at),
                    capture.local_date.isoformat(),
                    capture.source_version,
                    capture.content_hash,
                    capture.request_url,
                    capture.http_status,
                    capture.content_type,
                    capture.content_encoding,
                    len(capture.raw_bytes),
                    capture.raw_bytes,
                ),
            ).rowcount
            if not inserted:
                return False
            if capture.content_type == "application/json":
                connection.execute(
                    """
                    INSERT OR IGNORE INTO raw_snapshots(
                      snapshot_id, source, kind, source_time, observed_at, issued_at,
                      valid_from, valid_to, received_at, source_version, content_hash,
                      event_id, market_id, token_id, request_url, http_status,
                      duplicate_of_snapshot_id, payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        capture.capture_id,
                        capture.source,
                        capture.kind,
                        self._iso(capture.source_time),
                        self._iso(capture.observed_at),
                        self._iso(capture.issued_at),
                        None,
                        None,
                        self._iso(capture.received_at),
                        capture.source_version,
                        capture.content_hash,
                        None,
                        None,
                        None,
                        capture.request_url,
                        capture.http_status,
                        None,
                        self.dumps(payload),
                    ),
                )
            for item in observations or []:
                observed_at = item["observed_at"]
                observation_hash = content_hash(
                    {
                        "temperature_f": item.get("temperature_f"),
                        "temperature_c": item.get("temperature_c"),
                        "raw_unit": item.get("raw_unit"),
                        "raw_text": item.get("raw_text", ""),
                        "quality_control": item.get("quality_control", {}),
                    }
                )
                revision = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM weather_observations
                        WHERE station_id=? AND observed_at=? AND source=?
                        """,
                        (capture.station_id, self._iso(observed_at), capture.source),
                    ).fetchone()[0]
                ) + 1
                connection.execute(
                    """
                    INSERT OR IGNORE INTO weather_observations(
                      observation_id, snapshot_id, station_id, observed_at, received_at,
                      temperature_f, raw_text, source, temperature_c, raw_unit,
                      quality_control_json, source_version, revision, local_date
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        stable_id(
                            "observation",
                            capture.source,
                            observed_at,
                            observation_hash,
                        ),
                        capture.capture_id,
                        capture.station_id,
                        self._iso(observed_at),
                        self._iso(capture.received_at),
                        item.get("temperature_f"),
                        item.get("raw_text", ""),
                        capture.source,
                        item.get("temperature_c"),
                        item.get("raw_unit"),
                        self.dumps(item.get("quality_control", {})),
                        capture.source_version,
                        revision,
                        observed_at.astimezone(item["zone"]).date().isoformat(),
                    ),
                )
            if forecast_periods is not None:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO weather_forecasts VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        stable_id("forecast", capture.source, capture.content_hash),
                        capture.capture_id,
                        capture.source,
                        capture.station_id,
                        self._iso(capture.issued_at or capture.received_at),
                        self._iso(capture.received_at),
                        capture.local_date.isoformat(),
                        capture.source_version,
                        capture.content_hash,
                        len(forecast_periods),
                    ),
                )
                for item in forecast_periods:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO forecast_points(
                          forecast_point_id, snapshot_id, source, issued_at, valid_at,
                          received_at, temperature_f
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            stable_id(
                                "forecast_point",
                                capture.content_hash,
                                item["valid_at"],
                            ),
                            capture.capture_id,
                            capture.source,
                            self._iso(capture.issued_at or capture.received_at),
                            self._iso(item["valid_at"]),
                            self._iso(capture.received_at),
                            item["temperature_f"],
                        ),
                    )
        return True

    def save_settlement_evidence(self, evidence: SettlementEvidence) -> bool:
        screenshot_hash = None
        if evidence.screenshot_png is not None:
            import hashlib

            screenshot_hash = hashlib.sha256(evidence.screenshot_png).hexdigest()
        with self.transaction() as connection:
            return bool(
                connection.execute(
                    """
                    INSERT OR IGNORE INTO settlement_evidence VALUES(
                      ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        evidence.evidence_id,
                        evidence.capture_id,
                        evidence.station_id,
                        evidence.local_date.isoformat(),
                        self._iso(evidence.received_at),
                        self._iso(evidence.page_updated_at),
                        evidence.tmax_f,
                        self._iso(evidence.first_next_day_observed_at),
                        evidence.first_next_day_temperature_f,
                        evidence.table_text,
                        evidence.parse_status,
                        evidence.no_trade_reason,
                        int(evidence.finalized),
                        evidence.screenshot_png,
                        screenshot_hash,
                    ),
                ).rowcount
            )

    def latest_settlement_evidence(self, local_date: Any) -> sqlite3.Row | None:
        value = local_date.isoformat() if hasattr(local_date, "isoformat") else str(local_date)
        return self.connection.execute(
            """
            SELECT * FROM settlement_evidence
            WHERE local_date=? ORDER BY received_at DESC LIMIT 1
            """,
            (value,),
        ).fetchone()

    def pending_source_captures(self, limit: int = 2000) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT capture.* FROM source_captures AS capture
            WHERE NOT EXISTS (
              SELECT 1 FROM r2_export_items AS item WHERE item.source_id=capture.capture_id
            )
            ORDER BY capture.received_at LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def pending_screenshots(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM settlement_evidence AS evidence
            WHERE screenshot_png IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM r2_export_items AS item WHERE item.source_id=evidence.evidence_id
            )
            ORDER BY received_at LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def record_r2_export(
        self,
        *,
        export_id: str,
        export_type: str,
        source: str | None,
        local_date: str,
        object_key: str,
        sha256: str,
        size_bytes: int,
        source_ids: list[str],
        created_at: datetime,
        uploaded_at: datetime | None,
        status: str,
        error: str | None = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO r2_exports(
                  export_id, export_type, source, local_date, object_key, sha256,
                  size_bytes, source_ids_json, created_at, uploaded_at, status, error
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(object_key) DO UPDATE SET
                  uploaded_at=excluded.uploaded_at,
                  status=excluded.status,
                  error=excluded.error
                """,
                (
                    export_id,
                    export_type,
                    source,
                    local_date,
                    object_key,
                    sha256,
                    size_bytes,
                    self.dumps(source_ids),
                    self._iso(created_at),
                    self._iso(uploaded_at),
                    status,
                    error,
                ),
            )
            if status == "uploaded":
                for source_id in source_ids:
                    connection.execute(
                        "INSERT OR IGNORE INTO r2_export_items VALUES(?,?)",
                        (export_id, source_id),
                    )

    def rows_for_local_date(self, table: str, local_date: str) -> list[dict[str, Any]]:
        if table == "forecast_points":
            rows = self.connection.execute(
                """
                SELECT point.* FROM forecast_points AS point
                JOIN source_captures AS capture ON capture.capture_id=point.snapshot_id
                WHERE capture.local_date=? ORDER BY point.valid_at, point.forecast_point_id
                """,
                (local_date,),
            ).fetchall()
            return [dict(row) for row in rows]
        allowed = {
            "source_captures": "local_date",
            "weather_observations": "local_date",
            "weather_forecasts": "local_date",
            "settlement_evidence": "local_date",
        }
        if table not in allowed:
            raise ValueError(f"Unsupported export table: {table}")
        rows = self.connection.execute(
            f'SELECT * FROM "{table}" WHERE "{allowed[table]}"=? ORDER BY rowid',
            (local_date,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            for key, value in tuple(item.items()):
                if isinstance(value, bytes):
                    item[key] = None
            result.append(item)
        return result

    def r2_usage_summary(self) -> dict[str, Any]:
        row = self.connection.execute(
            """
            SELECT COALESCE(SUM(size_bytes), 0) AS bytes,
                   MIN(uploaded_at) AS first_upload,
                   MAX(uploaded_at) AS last_upload,
                   COUNT(*) AS object_count
            FROM r2_exports WHERE status='uploaded'
            """
        ).fetchone()
        return dict(row)

    def r2_exports_for_day(self, local_date: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT export_type, source, object_key, sha256, size_bytes, uploaded_at
            FROM r2_exports WHERE local_date=? AND status='uploaded'
            ORDER BY object_key
            """,
            (local_date,),
        ).fetchall()
        return [dict(row) for row in rows]

    def has_r2_export(self, export_type: str, local_date: str) -> bool:
        return (
            self.connection.execute(
                """
                SELECT 1 FROM r2_exports
                WHERE export_type=? AND local_date=? AND status='uploaded' LIMIT 1
                """,
                (export_type, local_date),
            ).fetchone()
            is not None
        )

    def collector_status(self) -> dict[str, Any]:
        now = datetime.now().astimezone()
        sources = []
        rows = self.connection.execute(
            """
            SELECT source, kind, MAX(received_at) AS last_received_at, COUNT(*) AS versions
            FROM source_captures GROUP BY source, kind ORDER BY source, kind
            """
        ).fetchall()
        for row in rows:
            received_at = datetime.fromisoformat(row["last_received_at"])
            sources.append(
                {
                    "source": row["source"],
                    "kind": row["kind"],
                    "last_received_at": row["last_received_at"],
                    "age_seconds": max(0.0, (now - received_at).total_seconds()),
                    "versions": int(row["versions"]),
                }
            )
        errors = self.connection.execute(
            """
            SELECT occurred_at, source, event_type, message FROM system_events
            WHERE context_json LIKE '%\"collector\":true%'
            ORDER BY occurred_at DESC LIMIT 10
            """
        ).fetchall()
        settlement = self.connection.execute(
            """
            SELECT local_date, received_at, tmax_f, parse_status, no_trade_reason,
                   finalized, first_next_day_observed_at
            FROM settlement_evidence ORDER BY received_at DESC LIMIT 1
            """
        ).fetchone()
        storage_rows = self.connection.execute(
            """
            SELECT local_date, source, COUNT(*) AS captures,
                   COALESCE(SUM(raw_size_bytes), 0) AS compressed_raw_bytes
            FROM source_captures
            WHERE local_date=(SELECT MAX(local_date) FROM source_captures)
            GROUP BY local_date, source ORDER BY source
            """
        ).fetchall()
        screenshot_row = self.connection.execute(
            """
            SELECT COALESCE(SUM(LENGTH(screenshot_png)), 0) AS screenshot_bytes
            FROM settlement_evidence
            WHERE local_date=(SELECT MAX(local_date) FROM settlement_evidence)
            """
        ).fetchone()
        return {
            "sources": sources,
            "recent_errors": [dict(row) for row in errors],
            "latest_settlement": dict(settlement) if settlement is not None else None,
            "latest_local_date_storage": {
                "sources": [dict(row) for row in storage_rows],
                "screenshot_bytes": int(screenshot_row["screenshot_bytes"]),
            },
            "r2": self.r2_usage_summary(),
        }

    @staticmethod
    def dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    def save_run(
        self,
        snapshots: tuple[RawSnapshot, ...],
        state: UnifiedState,
        estimate: ProbabilityEstimate,
        decision: Decision,
        broker: PaperBroker,
        account: PaperAccountSnapshot,
        source_snapshot_id: str,
        *,
        runner_id: str = "runner-local",
        cycle: int = 1,
    ) -> None:
        with self.transaction() as connection:
            for snapshot in snapshots:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO raw_snapshots(
                      snapshot_id, source, kind, source_time, observed_at, issued_at,
                      valid_from, valid_to, received_at, source_version, content_hash,
                      event_id, market_id, token_id, request_url, http_status, payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.source,
                        snapshot.kind,
                        self._iso(snapshot.source_time),
                        self._iso(snapshot.observed_at),
                        self._iso(snapshot.issued_at),
                        self._iso(snapshot.valid_from),
                        self._iso(snapshot.valid_to),
                        self._iso(snapshot.received_at),
                        snapshot.source_version,
                        snapshot.hash,
                        snapshot.event_id,
                        snapshot.market_id,
                        snapshot.token_id,
                        snapshot.request_url,
                        snapshot.http_status,
                        self.dumps(snapshot.payload),
                    ),
                )
            self._save_contract(connection, state.contract, source_snapshot_id, state.decision_time)
            for book in state.order_books.values():
                for side, levels in (("bid", book.bids), ("ask", book.asks)):
                    for index, level in enumerate(levels):
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO order_book_levels VALUES(?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                book.snapshot_id,
                                book.token_id,
                                book.market_id,
                                book.book_hash,
                                self._iso(book.exchange_time),
                                self._iso(book.received_at),
                                side,
                                index,
                                level.price,
                                level.size,
                            ),
                        )
            for item in state.observations:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO weather_observations(
                      observation_id, snapshot_id, station_id, observed_at, received_at,
                      temperature_f, raw_text
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        stable_id("observation", item.snapshot_id, item.observed_at),
                        item.snapshot_id,
                        item.station_id,
                        self._iso(item.observed_at),
                        self._iso(item.received_at),
                        item.temperature_f,
                        item.raw_text,
                    ),
                )
            for item in state.forecasts:
                connection.execute(
                    "INSERT OR REPLACE INTO forecast_points VALUES(?,?,?,?,?,?,?)",
                    (
                        stable_id("forecast", item.snapshot_id, item.valid_at),
                        item.snapshot_id,
                        item.source,
                        self._iso(item.issued_at),
                        self._iso(item.valid_at),
                        self._iso(item.received_at),
                        item.temperature_f,
                    ),
                )
            probability_summary = {
                "model_version": estimate.model_version,
                "baseline_tmax_f": estimate.baseline_tmax_f,
                "observed_tmax_f": estimate.observed_tmax_f,
                "mean_tmax_f": estimate.mean_tmax_f,
                "median_tmax_f": estimate.median_tmax_f,
                "interval_low_f": estimate.interval_low_f,
                "interval_high_f": estimate.interval_high_f,
                "probability_sum": estimate.probability_sum,
            }
            connection.execute(
                """
                INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(decision_id) DO UPDATE SET
                  status=excluded.status, overall_action=excluded.overall_action,
                  health_level=excluded.health_level, reason_codes_json=excluded.reason_codes_json,
                  probability_summary_json=excluded.probability_summary_json
                """,
                (
                    decision.decision_id,
                    self._iso(decision.decision_time),
                    decision.mode.value,
                    decision.contract_version_id,
                    decision.input_set_hash,
                    decision.model_version,
                    "in_progress",
                    decision.overall_action,
                    decision.health_level.value,
                    self.dumps([code.value for code in decision.reason_codes]),
                    self.dumps(probability_summary),
                    self._iso(decision.decision_time),
                ),
            )
            connection.execute(
                "DELETE FROM decision_inputs WHERE decision_id=?", (decision.decision_id,)
            )
            for snapshot_id in state.input_snapshot_ids:
                connection.execute(
                    "INSERT INTO decision_inputs VALUES(?,?,?)",
                    (decision.decision_id, snapshot_id, "decision_state"),
                )
            for outcome in decision.outcomes:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO decision_outcomes
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        outcome.decision_id,
                        outcome.bin_id,
                        outcome.label,
                        outcome.model_probability,
                        outcome.best_bid,
                        outcome.best_ask,
                        outcome.mid,
                        outcome.executable_quantity,
                        outcome.executable_price,
                        outcome.executable_depth,
                        outcome.gross_edge,
                        outcome.fee,
                        outcome.slippage,
                        outcome.uncertainty_buffer,
                        outcome.net_edge,
                        outcome.action.value,
                        int(outcome.risk_approved),
                        self.dumps([code.value for code in outcome.reason_codes]),
                        outcome.paper_position,
                    ),
                )
            for check in state.health.checks:
                connection.execute(
                    "INSERT OR REPLACE INTO data_health VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        decision.decision_id,
                        check.source,
                        check.level.value,
                        self._iso(check.received_at),
                        self._iso(check.source_time),
                        check.age_seconds,
                        self.dumps([code.value for code in check.reason_codes]),
                        check.duplicate_count,
                        check.out_of_order_count,
                        check.gap_count,
                        check.message,
                    ),
                )
            self._save_paper(connection, broker, account, decision)
            connection.execute(
                """
                INSERT OR REPLACE INTO runner_heartbeats VALUES(?,?,?,?,?,?,?)
                """,
                (
                    stable_id("heartbeat", runner_id, cycle, decision.decision_id),
                    runner_id,
                    decision.mode.value,
                    cycle,
                    self._iso(decision.decision_time),
                    decision.decision_id,
                    "ok",
                ),
            )
            connection.execute(
                "UPDATE decisions SET status='complete' WHERE decision_id=?",
                (decision.decision_id,),
            )

    def _save_contract(
        self,
        connection: sqlite3.Connection,
        contract: MarketContract,
        source_id: str,
        received_at: Any,
    ) -> None:
        connection.execute(
            """
            INSERT OR REPLACE INTO contract_versions
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                contract.contract_version_id,
                contract.event_id,
                contract.event_slug,
                contract.event_title,
                contract.market_url,
                contract.local_day.isoformat(),
                contract.city_code,
                contract.station_id,
                contract.timezone,
                contract.metric,
                contract.unit,
                contract.rounding,
                self._iso(contract.observation_start),
                self._iso(contract.observation_end),
                contract.settlement_source,
                contract.rule_text,
                contract.rule_version,
                contract.rule_hash,
                contract.parse_status,
                self.dumps([code.value for code in contract.ambiguities]),
                int(contract.event_active),
                int(contract.event_closed),
                source_id,
                self._iso(received_at),
            ),
        )
        for item in contract.bins:
            connection.execute(
                """
                INSERT OR REPLACE INTO contract_bins
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    item.bin_id,
                    contract.contract_version_id,
                    item.label,
                    item.ordinal,
                    item.market_id,
                    item.condition_id,
                    item.yes_token_id,
                    item.no_token_id,
                    item.lower_bound,
                    item.upper_bound,
                    int(item.lower_inclusive),
                    int(item.upper_inclusive),
                    int(item.active),
                    int(item.closed),
                    int(item.accepting_orders),
                    item.tick_size,
                    item.minimum_order_size,
                    item.fee_rate,
                    item.fee_exponent,
                ),
            )

    def _save_paper(
        self,
        connection: sqlite3.Connection,
        broker: PaperBroker,
        account: PaperAccountSnapshot,
        decision: Decision,
    ) -> None:
        for order in broker.orders:
            connection.execute(
                "INSERT OR REPLACE INTO paper_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    order.order_id,
                    order.decision_id,
                    order.bin_id,
                    order.side,
                    order.limit_price,
                    order.quantity,
                    order.filled_quantity,
                    order.average_fill_price,
                    order.reserved_cash,
                    order.status.value,
                    self._iso(order.created_at),
                    self._iso(order.updated_at),
                    order.stale_after_cycle,
                    self.dumps([code.value for code in order.reason_codes]),
                ),
            )
        for fill in broker.fills:
            connection.execute(
                "INSERT OR IGNORE INTO paper_fills VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.decision_id,
                    fill.bin_id,
                    fill.book_snapshot_id,
                    fill.book_hash,
                    fill.side,
                    fill.price,
                    fill.quantity,
                    fill.fee,
                    self._iso(fill.filled_at),
                    fill.level_index,
                ),
            )
        connection.execute(
            "INSERT OR REPLACE INTO paper_accounts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                decision.decision_id,
                account.cash,
                account.reserved_cash,
                account.used_notional,
                account.realized_pnl,
                account.unrealized_pnl,
                account.total_pnl,
                account.nav,
                self.dumps(account.positions),
                self.dumps(account.scenario_pnl),
                account.mark_source,
                self._iso(decision.decision_time),
            ),
        )

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
    UnifiedState,
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
                    "INSERT OR REPLACE INTO weather_observations VALUES(?,?,?,?,?,?,?)",
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

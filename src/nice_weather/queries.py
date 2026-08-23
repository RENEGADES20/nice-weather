from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nice_weather.store import WeatherStore


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


class DashboardQuery:
    """The only read path used by the Streamlit application."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def _query(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with WeatherStore(self.database_path, read_only=True) as store:
            return _rows(store.connection.execute(sql, parameters).fetchall())

    def get_latest_decision_summary(self) -> dict[str, Any] | None:
        rows = self._query(
            """
            SELECT d.*, c.event_id, c.event_slug, c.event_title, c.market_url,
                   c.local_day, c.city_code, c.station_id, c.timezone, c.unit,
                   c.parse_status, c.rule_version, c.event_active, c.event_closed,
                   p.cash, p.used_notional, p.realized_pnl, p.unrealized_pnl,
                   p.total_pnl, p.nav
            FROM decisions d
            JOIN contract_versions c USING(contract_version_id)
            LEFT JOIN paper_accounts p USING(decision_id)
            WHERE d.status='complete'
            ORDER BY d.decision_time DESC, d.decision_id DESC LIMIT 1
            """
        )
        if not rows:
            return None
        row = rows[0]
        row["reason_codes"] = json.loads(row.pop("reason_codes_json"))
        row["probability_summary"] = json.loads(row.pop("probability_summary_json"))
        return row

    def get_contract_view(self, decision_id: str) -> dict[str, Any]:
        contract = self._query(
            """
            SELECT c.* FROM contract_versions c JOIN decisions d USING(contract_version_id)
            WHERE d.decision_id=?
            """,
            (decision_id,),
        )
        bins = self._query(
            """
            SELECT b.* FROM contract_bins b JOIN decisions d USING(contract_version_id)
            WHERE d.decision_id=? ORDER BY b.ordinal
            """,
            (decision_id,),
        )
        if not contract:
            return {"contract": None, "bins": []}
        contract[0]["ambiguities"] = json.loads(contract[0].pop("ambiguities_json"))
        return {"contract": contract[0], "bins": bins}

    def get_outcome_snapshot(self, decision_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT o.*, b.ordinal, b.market_id, b.condition_id, b.yes_token_id,
                   b.minimum_order_size, b.tick_size
            FROM decision_outcomes o JOIN contract_bins b USING(bin_id)
            WHERE o.decision_id=? ORDER BY b.ordinal
            """,
            (decision_id,),
        )
        for row in rows:
            row["reason_codes"] = json.loads(row.pop("reason_codes_json"))
        return rows

    def get_weather_path(self, decision_id: str) -> dict[str, list[dict[str, Any]]]:
        observations = self._query(
            """
            SELECT w.* FROM weather_observations w
            JOIN decision_inputs i USING(snapshot_id)
            WHERE i.decision_id=? ORDER BY w.observed_at
            """,
            (decision_id,),
        )
        forecasts = self._query(
            """
            SELECT f.* FROM forecast_points f
            JOIN decision_inputs i USING(snapshot_id)
            WHERE i.decision_id=? ORDER BY f.valid_at
            """,
            (decision_id,),
        )
        return {"observations": observations, "forecasts": forecasts}

    def get_outcome_history(self, bin_id: str) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT d.decision_id, d.decision_time, o.*, f.filled_at, f.price AS fill_price
            FROM decision_outcomes o JOIN decisions d USING(decision_id)
            LEFT JOIN paper_fills f ON f.decision_id=d.decision_id AND f.bin_id=o.bin_id
            WHERE o.bin_id=? AND d.status='complete'
            ORDER BY d.decision_time
            """,
            (bin_id,),
        )

    def get_order_book(self, decision_id: str, bin_id: str) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT l.* FROM order_book_levels l
            JOIN decision_inputs i USING(snapshot_id)
            JOIN contract_bins b ON b.yes_token_id=l.token_id
            WHERE i.decision_id=? AND b.bin_id=?
            ORDER BY l.side, l.level_index
            """,
            (decision_id, bin_id),
        )

    def get_paper_view(self, decision_id: str) -> dict[str, Any]:
        accounts = self._query("SELECT * FROM paper_accounts WHERE decision_id=?", (decision_id,))
        orders = self._query(
            "SELECT * FROM paper_orders WHERE decision_id=? ORDER BY created_at, order_id",
            (decision_id,),
        )
        fills = self._query(
            "SELECT * FROM paper_fills WHERE decision_id=? ORDER BY filled_at, fill_id",
            (decision_id,),
        )
        account = accounts[0] if accounts else None
        if account:
            account["positions"] = json.loads(account.pop("positions_json"))
            account["scenario_pnl"] = json.loads(account.pop("scenario_pnl_json"))
        return {"account": account, "orders": orders, "fills": fills}

    def get_health_view(self, decision_id: str) -> dict[str, Any]:
        checks = self._query(
            "SELECT * FROM data_health WHERE decision_id=? ORDER BY source", (decision_id,)
        )
        for row in checks:
            row["reason_codes"] = json.loads(row.pop("reason_codes_json"))
        heartbeat = self._query(
            """
            SELECT * FROM runner_heartbeats WHERE decision_id=?
            ORDER BY occurred_at DESC LIMIT 1
            """,
            (decision_id,),
        )
        events = self._query("SELECT * FROM system_events ORDER BY occurred_at DESC LIMIT 20")
        return {
            "checks": checks,
            "heartbeat": heartbeat[0] if heartbeat else None,
            "events": events,
        }

    def list_decisions(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT d.decision_id, d.decision_time, d.mode, d.overall_action,
                   d.health_level, d.reason_codes_json, c.event_title,
                   o.label AS outcome, o.action AS signal,
                   o.risk_approved, o.reason_codes_json AS outcome_reason_codes_json,
                   p.order_id AS paper_order_id,
                   (SELECT GROUP_CONCAT(i.snapshot_id, ',')
                    FROM decision_inputs i WHERE i.decision_id=d.decision_id) AS snapshot_refs
            FROM decisions d JOIN contract_versions c USING(contract_version_id)
            LEFT JOIN decision_outcomes o USING(decision_id)
            LEFT JOIN paper_orders p ON p.decision_id=d.decision_id AND p.bin_id=o.bin_id
            WHERE d.status='complete' ORDER BY d.decision_time DESC LIMIT ?
            """,
            (limit,),
        )
        for row in rows:
            row["reason_codes"] = json.loads(row.pop("reason_codes_json"))
            raw_outcome_reasons = row.pop("outcome_reason_codes_json")
            row["outcome_reason_codes"] = (
                json.loads(raw_outcome_reasons) if raw_outcome_reasons else []
            )
        return rows

    def get_decision_trace(self, decision_id: str) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT r.snapshot_id, r.source, r.kind, r.source_time, r.observed_at,
                   r.issued_at, r.valid_from, r.valid_to, r.received_at,
                   r.source_version, r.content_hash, r.event_id, r.market_id,
                   r.token_id, r.request_url, r.http_status
            FROM raw_snapshots r JOIN decision_inputs i USING(snapshot_id)
            WHERE i.decision_id=? ORDER BY r.source, r.kind, r.snapshot_id
            """,
            (decision_id,),
        )

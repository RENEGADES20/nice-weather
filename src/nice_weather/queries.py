from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from nice_weather.store import WeatherStore


def _rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _object_day(value: object, object_timezone: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(ZoneInfo(object_timezone)).date().isoformat()


def object_day_bounds(
    object_local_date: date, horizon_days: int, object_timezone: str
) -> tuple[datetime, datetime]:
    if horizon_days not in (1, 2, 3, 5):
        raise ValueError("horizon_days must be one of 1, 2, 3 or 5")
    zone = ZoneInfo(object_timezone)
    start = datetime.combine(object_local_date, time.min, zone).astimezone(UTC)
    end = datetime.combine(object_local_date, time.min, zone) + timedelta(days=horizon_days)
    return start, end.astimezone(UTC)


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
            WITH weather_ids(capture_id) AS (
              SELECT capture_id FROM decision_weather_inputs WHERE decision_id=?
              UNION
              SELECT input.value FROM model_predictions AS prediction
              JOIN weather_feature_snapshots AS feature USING(feature_snapshot_id)
              JOIN json_each(feature.input_capture_ids_json) AS input
              WHERE prediction.decision_id=?
            )
            SELECT w.* FROM weather_observations w
            WHERE w.legacy_snapshot_id IN (
              SELECT snapshot_id FROM decision_inputs WHERE decision_id=?
            ) OR w.capture_id IN (
              SELECT capture_id FROM weather_ids
            )
            ORDER BY w.observed_at
            """,
            (decision_id, decision_id, decision_id),
        )
        forecasts = self._query(
            """
            WITH weather_ids(capture_id) AS (
              SELECT capture_id FROM decision_weather_inputs WHERE decision_id=?
              UNION
              SELECT input.value FROM model_predictions AS prediction
              JOIN weather_feature_snapshots AS feature USING(feature_snapshot_id)
              JOIN json_each(feature.input_capture_ids_json) AS input
              WHERE prediction.decision_id=?
            )
            SELECT f.* FROM forecast_points f
            WHERE f.legacy_snapshot_id IN (
              SELECT snapshot_id FROM decision_inputs WHERE decision_id=?
            ) OR f.capture_id IN (
              SELECT capture_id FROM weather_ids
            )
            ORDER BY f.valid_at
            """,
            (decision_id, decision_id, decision_id),
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

    def list_market_days(self, limit: int = 90) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT local_day,event_id,event_title,timezone,event_closed
            FROM contract_versions
            GROUP BY local_day,event_id
            ORDER BY local_day DESC,MAX(received_at) DESC LIMIT ?
            """,
            (limit,),
        )

    def get_event_bins(self, event_id: str) -> list[dict[str, Any]]:
        return self._query(
            """
            SELECT b.* FROM contract_bins b
            JOIN contract_versions c USING(contract_version_id)
            WHERE c.event_id=? AND c.contract_version_id=(
              SELECT contract_version_id FROM contract_versions
              WHERE event_id=? ORDER BY received_at DESC LIMIT 1
            )
            ORDER BY b.ordinal
            """,
            (event_id, event_id),
        )

    def get_latest_event_probabilities(self, event_id: str) -> dict[str, float]:
        rows = self._query(
            """
            SELECT o.bin_id,o.model_probability
            FROM decision_outcomes o
            WHERE o.decision_id=(
              SELECT d.decision_id FROM decisions d
              JOIN contract_versions c USING(contract_version_id)
              WHERE c.event_id=? AND d.status='complete'
              ORDER BY d.decision_time DESC,d.decision_id DESC LIMIT 1
            )
            """,
            (event_id,),
        )
        return {str(row["bin_id"]): float(row["model_probability"]) for row in rows}

    def get_weather_timeline(
        self,
        object_local_date: date,
        horizon_days: int,
        as_of: datetime,
        object_timezone: str,
    ) -> dict[str, list[dict[str, Any]]]:
        start, end = object_day_bounds(object_local_date, horizon_days, object_timezone)
        as_of_text = as_of.astimezone(UTC).isoformat()
        observations = self._query(
            """
            WITH ranked AS (
              SELECT w.*,ROW_NUMBER() OVER(
                PARTITION BY station_id,source,observed_at ORDER BY revision DESC,received_at DESC
              ) AS rank
              FROM weather_observations w
              WHERE station_id='KLGA' AND observed_at>=? AND observed_at<? AND received_at<=?
            )
            SELECT * FROM ranked WHERE rank=1 ORDER BY observed_at,source
            """,
            (start.isoformat(), end.isoformat(), as_of_text),
        )
        captures = self._query(
            """
            SELECT f.capture_id FROM weather_forecasts f
            WHERE f.source='nws' AND f.station_id='KLGA' AND f.received_at<=?
              AND EXISTS(
                SELECT 1 FROM forecast_points p
                WHERE p.capture_id=f.capture_id AND p.valid_at>=? AND p.valid_at<?
              )
            ORDER BY f.received_at DESC LIMIT 1
            """,
            (as_of_text, start.isoformat(), end.isoformat()),
        )
        forecasts = (
            self._query(
                """
                SELECT * FROM forecast_points
                WHERE capture_id=? AND valid_at>=? AND valid_at<? AND received_at<=?
                ORDER BY valid_at
                """,
                (captures[0]["capture_id"], start.isoformat(), end.isoformat(), as_of_text),
            )
            if captures
            else []
        )
        settlement = self._query(
            """
            SELECT * FROM settlement_rows
            WHERE station_id='KLGA' AND observed_at>=? AND observed_at<? AND received_at<=?
            ORDER BY received_at,observed_at
            """,
            (start.isoformat(), end.isoformat(), as_of_text),
        )

        running_by_day: dict[str, float] = {}
        running_tmax = []
        for item in settlement:
            value = float(item["temperature_f"])
            local_day = str(
                item.get("object_local_date")
                or item.get("local_date")
                or _object_day(item["observed_at"], object_timezone)
            )
            running = running_by_day.get(local_day)
            if running is None or value > running:
                running_by_day[local_day] = value
                running_tmax.append(
                    {
                        "observed_at": item["observed_at"],
                        "received_at": item["received_at"],
                        "temperature_f": value,
                        "object_local_date": local_day,
                    }
                )
        return {
            "observations": observations,
            "forecasts": forecasts,
            "running_tmax": running_tmax,
        }

    def repricing_weather_version(self) -> tuple[int, ...]:
        rows = self._query(
            "SELECT (SELECT COALESCE(MAX(rowid),0) FROM weather_observations) AS observations,"
            "(SELECT COALESCE(MAX(rowid),0) FROM forecast_points) AS forecasts,"
            "(SELECT COALESCE(MAX(rowid),0) FROM settlement_rows) AS settlement"
        )
        return tuple(rows[0].values())

    def get_repricing_ticks(
        self,
        event_id: str,
        bin_id: str,
        start: datetime,
        end: datetime,
        as_of: datetime,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        parameters: list[Any] = [
            event_id,
            bin_id,
            start.isoformat(),
            end.isoformat(),
            as_of.isoformat(),
        ]
        cursor_sql = ""
        if cursor:
            stamp, identifier = cursor.split("|", 1)
            cursor_sql = "AND (received_at,tick_id)>(?,?)"
            parameters.extend((stamp, identifier))
        rows = self._query(
            "SELECT * FROM market_top_ticks WHERE event_id=? AND bin_id=? "
            "AND received_at>=? AND exchange_event_at<? AND received_at<=? "
            f"{cursor_sql} ORDER BY received_at,tick_id",
            tuple(parameters),
        )
        if rows:
            cursor = f"{rows[-1]['received_at']}|{rows[-1]['tick_id']}"
        return {"ticks": rows, "cursor": cursor}

    def get_repricing_weather_history(
        self,
        object_local_date: date,
        horizon_days: int,
        as_of: datetime,
        object_timezone: str,
        observation_age_seconds: int,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return versioned raw inputs used to rebuild the information known at each minute."""
        start, end = object_day_bounds(object_local_date, horizon_days, object_timezone)
        as_of_text = as_of.astimezone(UTC).isoformat()
        observation_start = start - timedelta(seconds=observation_age_seconds)
        observations = self._query(
            """
            SELECT * FROM weather_observations
            WHERE station_id='KLGA' AND observed_at>=? AND observed_at<? AND received_at<=?
              AND source IN ('aviationweather','nws')
            ORDER BY received_at,observed_at,revision,observation_id
            """,
            (observation_start.isoformat(), end.isoformat(), as_of_text),
        )
        forecasts = self._query(
            """
            SELECT p.*
            FROM forecast_points p
            WHERE p.source='nws' AND p.valid_at>=? AND p.valid_at<? AND p.received_at<=?
            ORDER BY p.received_at,p.capture_id,p.legacy_snapshot_id,p.valid_at,
                     p.forecast_point_id
            """,
            (
                (start - timedelta(days=1)).isoformat(),
                (end + timedelta(days=1)).isoformat(),
                as_of_text,
            ),
        )
        settlement = self._query(
            """
            SELECT * FROM settlement_rows
            WHERE station_id='KLGA' AND observed_at>=? AND observed_at<? AND received_at<=?
            ORDER BY received_at,observed_at,row_id
            """,
            (observation_start.isoformat(), end.isoformat(), as_of_text),
        )
        return {
            "observations": observations,
            "forecasts": forecasts,
            "settlement_rows": settlement,
        }

    def get_forecast_revision_events(
        self, object_local_date: date, as_of: datetime, object_timezone: str
    ) -> list[dict[str, Any]]:
        zone = ZoneInfo(object_timezone)
        start = datetime.combine(object_local_date, time.min, zone).astimezone(UTC)
        end = (datetime.combine(object_local_date, time.min, zone) + timedelta(days=1)).astimezone(
            UTC
        )
        rows = self._query(
            """
            SELECT f.capture_id,f.issued_at,f.received_at,f.content_hash,
                   MAX(p.temperature_f) AS forecast_tmax_f,
                   GROUP_CONCAT(p.valid_at || ':' || p.temperature_f,'|') AS path
            FROM weather_forecasts f JOIN forecast_points p USING(capture_id)
            WHERE f.station_id='KLGA' AND f.received_at<=?
              AND p.valid_at>=? AND p.valid_at<?
            GROUP BY f.capture_id,f.issued_at,f.received_at,f.content_hash
            ORDER BY f.received_at
            """,
            (as_of.astimezone(UTC).isoformat(), start.isoformat(), end.isoformat()),
        )
        events = []
        previous: tuple[float, str] | None = None
        for row in rows:
            current = (float(row["forecast_tmax_f"]), str(row["path"]))
            if previous is not None and current != previous:
                events.append({**row, "type": "forecast_revised"})
            previous = current
        return events

    def get_market_bin_history(
        self,
        event_id: str,
        bin_ids: list[str],
        start_at: datetime,
        end_at: datetime,
        after_cursor: str | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        if not bin_ids:
            return {"ticks": [], "cursor": after_cursor}
        placeholders = ",".join("?" for _ in bin_ids)
        page_size = 20_000
        cursor = after_cursor
        result: list[dict[str, Any]] = []
        while True:
            parameters: list[Any] = [
                event_id,
                *bin_ids,
                start_at.astimezone(UTC).isoformat(),
                end_at.astimezone(UTC).isoformat(),
            ]
            as_of_sql = ""
            if as_of is not None:
                as_of_sql = "AND received_at<=?"
                parameters.append(as_of.astimezone(UTC).isoformat())
            cursor_sql = ""
            if cursor:
                cursor_time, cursor_id = cursor.split("|", 1)
                cursor_sql = "AND (received_at>? OR (received_at=? AND tick_id>?))"
                parameters.extend((cursor_time, cursor_time, cursor_id))
            rows = self._query(
                f"""
                SELECT * FROM market_top_ticks
                WHERE event_id=? AND bin_id IN ({placeholders})
                  AND exchange_event_at>=? AND exchange_event_at<?
                  {as_of_sql} {cursor_sql}
                ORDER BY received_at,tick_id LIMIT {page_size}
                """,
                tuple(parameters),
            )
            result.extend(rows)
            if not rows:
                break
            cursor = f"{rows[-1]['received_at']}|{rows[-1]['tick_id']}"
            if len(rows) < page_size:
                break
        return {"ticks": result, "cursor": cursor}

    def get_tmax_knowledge_events(
        self, object_local_date: date, as_of: datetime
    ) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT observed_at,received_at,temperature_f,source
            FROM weather_observations
            WHERE COALESCE(object_local_date,local_date)=? AND received_at<=?
            ORDER BY received_at,observed_at
            """,
            (object_local_date.isoformat(), as_of.astimezone(UTC).isoformat()),
        )
        running = None
        result = []
        for row in rows:
            value = float(row["temperature_f"])
            if running is None or value > running:
                running = value
                result.append({**row, "running_tmax_f": value})
        return result

    def get_price_in_analysis(
        self, event_id: str, threshold: float, quantity: float
    ) -> list[dict[str, Any]]:
        from nice_weather.config import load_city_config
        from nice_weather.research import tmax_repricing_report

        days = self._query(
            "SELECT MIN(local_day) first_day,MAX(local_day) last_day "
            "FROM contract_versions WHERE event_id=?",
            (event_id,),
        )[0]
        if not days["first_day"]:
            return []
        report = tmax_repricing_report(
            self.database_path,
            load_city_config(),
            start_date=date.fromisoformat(days["first_day"]),
            end_date=date.fromisoformat(days["last_day"]),
            quantity=quantity,
            thresholds=(threshold,),
        )
        return report["events"]

    def get_order_book(self, decision_id: str, bin_id: str) -> list[dict[str, Any]]:
        quotes = self._query(
            """
            SELECT q.top_levels_json FROM execution_quotes q
            JOIN decision_outcomes o ON o.quote_id=q.quote_id
            WHERE o.decision_id=? AND o.bin_id=? LIMIT 1
            """,
            (decision_id, bin_id),
        )
        if quotes:
            payload = json.loads(quotes[0]["top_levels_json"])
            return [
                {"side": side[:-1], "level_index": index, **level}
                for side in ("bids", "asks")
                for index, level in enumerate(payload.get(side, []))
            ]
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

    def get_execution_quote(self, decision_id: str, bin_id: str) -> dict[str, Any] | None:
        rows = self._query(
            """
            SELECT q.quote_id,q.market_id,q.token_id,q.requested_at,q.received_at,
                   q.best_bid,q.best_ask,q.spread,q.target_quantity,q.bid_vwap,
                   q.ask_vwap,q.bid_depth,q.ask_depth,q.status,q.error_reason
            FROM execution_quotes q
            JOIN decision_outcomes o ON o.quote_id=q.quote_id
            WHERE o.decision_id=? AND o.bin_id=?
            LIMIT 1
            """,
            (decision_id, bin_id),
        )
        return rows[0] if rows else None

    def get_model_context(self, decision_id: str) -> dict[str, Any]:
        features = self._query(
            """
            SELECT * FROM weather_feature_snapshots
            WHERE feature_snapshot_id=(
              SELECT feature_snapshot_id FROM model_predictions WHERE decision_id=? LIMIT 1
            )
            """,
            (decision_id,),
        )
        prediction = self._query(
            "SELECT * FROM model_predictions WHERE decision_id=? LIMIT 1", (decision_id,)
        )
        if features:
            features[0]["input_capture_ids"] = json.loads(features[0].pop("input_capture_ids_json"))
            features[0]["features"] = json.loads(features[0].pop("features_json"))
            features[0]["missing_flags"] = json.loads(features[0].pop("missing_flags_json"))
        return {
            "features": features[0] if features else None,
            "prediction": prediction[0] if prediction else None,
        }

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

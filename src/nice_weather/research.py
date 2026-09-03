from __future__ import annotations

import gzip
import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from astral import LocationInfo
from astral.sun import elevation, sun

from nice_weather.collector import parse_settlement_page
from nice_weather.config import CityConfig
from nice_weather.decision import taker_fee_per_share
from nice_weather.domain import content_hash, stable_id
from nice_weather.store import WeatherStore


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def repair_settlement_dates(
    database_path: str | Path, config: CityConfig, *, apply: bool
) -> dict[str, Any]:
    zone = config.zone
    with WeatherStore(database_path, read_only=not apply) as store:
        stored_rows = store.connection.execute(
            """
            SELECT row_id,evidence_id,local_date,object_local_date,observed_at,
                   received_at,temperature_f
            FROM settlement_rows ORDER BY received_at,row_index
            """
        ).fetchall()
        existing_ids = {str(row["row_id"]) for row in stored_rows}
        rows_by_evidence: dict[str, list[sqlite3.Row]] = {}
        for row in stored_rows:
            rows_by_evidence.setdefault(str(row["evidence_id"]), []).append(row)
        assignments = []
        derived_rows = []
        for row in stored_rows:
            expected = _parse(str(row["observed_at"])).astimezone(zone).date().isoformat()
            current = str(row["object_local_date"] or row["local_date"])
            if current != expected or str(row["local_date"]) != expected:
                assignments.append(
                    {
                        "row_id": str(row["row_id"]),
                        "from": current,
                        "to": expected,
                    }
                )

        evidence_rows = store.connection.execute(
            """
            SELECT e.evidence_id,e.capture_id,e.station_id,e.local_date,
                   e.object_local_date,e.object_timezone,e.tmax_f,e.finalized,
                   e.received_at,e.parser_version,c.raw_blob,c.content_encoding
            FROM settlement_evidence e JOIN source_captures c USING(capture_id)
            ORDER BY e.received_at
            """
        ).fetchall()
        parse_errors = []
        reconstructed_rows = []
        for item in evidence_rows:
            target = str(item["local_date"])
            parsed_rows: list[tuple[datetime, float]] = []
            try:
                raw = bytes(item["raw_blob"])
                if item["content_encoding"] == "gzip":
                    raw = gzip.decompress(raw)
                parsed = parse_settlement_page(
                    raw.decode("utf-8"), date.fromisoformat(target), zone
                )
                if parsed.parse_status == "parsed":
                    parsed_rows = list(parsed.rows)
                else:
                    parse_errors.append(str(item["evidence_id"]))
            except (OSError, UnicodeDecodeError, ValueError):
                parse_errors.append(str(item["evidence_id"]))
            if not parsed_rows:
                parsed_rows = [
                    (_parse(str(row["observed_at"])), float(row["temperature_f"]))
                    for row in rows_by_evidence.get(str(item["evidence_id"]), [])
                ]
            for index, (observed_at, temperature_f) in enumerate(parsed_rows):
                observed_text = observed_at.isoformat()
                row_hash = content_hash(
                    {"observed_at": observed_text, "temperature_f": temperature_f}
                )
                reconstructed = {
                    "row_id": stable_id(
                        "settlement_row", item["evidence_id"], index, row_hash
                    ),
                    "evidence_id": str(item["evidence_id"]),
                    "capture_id": str(item["capture_id"]),
                    "station_id": str(item["station_id"]),
                    "object_local_date": observed_at.astimezone(zone).date().isoformat(),
                    "observed_at": observed_text,
                    "received_at": _parse(str(item["received_at"])),
                    "temperature_f": temperature_f,
                    "row_index": index,
                    "row_hash": row_hash,
                }
                reconstructed_rows.append(reconstructed)
                derived_rows.append(reconstructed)

        evidence_changes = []
        for item in evidence_rows:
            target = str(item["local_date"])
            as_of = _parse(str(item["received_at"]))
            visible = [row for row in derived_rows if row["received_at"] <= as_of]
            latest_by_object_time: dict[str, dict[str, Any]] = {}
            for row in visible:
                latest_by_object_time[row["observed_at"]] = row
            values = [
                row["temperature_f"]
                for row in latest_by_object_time.values()
                if row["object_local_date"] == target
            ]
            later = [
                row
                for row in latest_by_object_time.values()
                if row["object_local_date"] > target
            ]
            tmax = max(values) if values else None
            finalized = bool(later)
            current_tmax = item["tmax_f"]
            if (
                current_tmax != tmax
                or bool(item["finalized"]) != finalized
                or item["object_timezone"] != config.timezone
                or item["parser_version"] != "weather-gov-hourly-v3"
            ):
                evidence_changes.append(
                    {
                        "evidence_id": str(item["evidence_id"]),
                        "local_date": target,
                        "from_tmax_f": current_tmax,
                        "to_tmax_f": tmax,
                        "finalized": finalized,
                    }
                )

        if apply:
            with store.transaction() as connection:
                for change in assignments:
                    connection.execute(
                        """
                        UPDATE settlement_rows
                        SET local_date=?,object_local_date=?,object_timezone=?
                        WHERE row_id=?
                        """,
                        (change["to"], change["to"], config.timezone, change["row_id"]),
                    )
                for row in reconstructed_rows:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO settlement_rows(
                          row_id,evidence_id,capture_id,station_id,local_date,
                          object_local_date,object_timezone,observed_at,received_at,
                          temperature_f,row_index,row_hash
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            row["row_id"],
                            row["evidence_id"],
                            row["capture_id"],
                            row["station_id"],
                            row["object_local_date"],
                            row["object_local_date"],
                            config.timezone,
                            row["observed_at"],
                            row["received_at"].isoformat(),
                            row["temperature_f"],
                            row["row_index"],
                            row["row_hash"],
                        ),
                    )
                for change in evidence_changes:
                    connection.execute(
                        """
                        UPDATE settlement_evidence
                        SET object_local_date=?,object_timezone=?,tmax_f=?,finalized=?,
                            parser_version='weather-gov-hourly-v3'
                        WHERE evidence_id=?
                        """,
                        (
                            change["local_date"],
                            config.timezone,
                            change["to_tmax_f"],
                            int(change["finalized"]),
                            change["evidence_id"],
                        ),
                    )
                label_rows = connection.execute(
                    "SELECT label_id,station_id,local_date FROM weather_daily_labels"
                ).fetchall()
                for label in label_rows:
                    evidence = connection.execute(
                        """
                        SELECT evidence_id,tmax_f,received_at FROM settlement_evidence
                        WHERE station_id=? AND object_local_date=? AND finalized=1
                          AND tmax_f IS NOT NULL
                        ORDER BY received_at DESC LIMIT 1
                        """,
                        (label["station_id"], label["local_date"]),
                    ).fetchone()
                    if evidence is None:
                        continue
                    label_hash = content_hash(
                        {
                            "station_id": label["station_id"],
                            "local_date": label["local_date"],
                            "official_tmax_f": evidence["tmax_f"],
                            "evidence_id": evidence["evidence_id"],
                        }
                    )
                    connection.execute(
                        """
                        UPDATE weather_daily_labels
                        SET official_tmax_f=?,evidence_id=?,finalized_at=?,
                            label_version='weather-gov-hourly-v3',label_hash=?
                        WHERE label_id=?
                        """,
                        (
                            evidence["tmax_f"],
                            evidence["evidence_id"],
                            evidence["received_at"],
                            label_hash,
                            label["label_id"],
                        ),
                    )

        digest = content_hash({"rows": assignments, "evidence": evidence_changes})
        return {
            "mode": "apply" if apply else "dry-run",
            "object_timezone": config.timezone,
            "rows_scanned": len(stored_rows),
            "raw_captures_scanned": len(evidence_rows),
            "raw_parse_errors": len(parse_errors),
            "reconstructed_rows": len(reconstructed_rows),
            "missing_rows": sum(
                str(row["row_id"]) not in existing_ids for row in reconstructed_rows
            ),
            "row_date_changes": len(assignments),
            "evidence_changes": len(evidence_changes),
            "change_checksum": digest,
            "sample": assignments[:20],
        }


def _contract_temperature(value: float) -> int:
    return math.floor(value + 0.5)


def _matching_bin(bins: list[sqlite3.Row], value: int) -> sqlite3.Row | None:
    for item in bins:
        low = item["lower_bound"]
        high = item["upper_bound"]
        if (low is None or value >= float(low)) and (high is None or value <= float(high)):
            return item
    return None


def _persistent_threshold(
    ticks: list[dict[str, Any]], threshold: float, start: datetime, *, rising: bool = True
) -> datetime | None:
    def matches(value: float | None) -> bool:
        if value is None:
            return False
        return value >= threshold if rising else value <= threshold

    for index, tick in enumerate(ticks):
        when = tick["time"]
        if when < start or not matches(tick["mid"]):
            continue
        deadline = when + timedelta(seconds=60)
        following = [item for item in ticks[index:] if item["time"] <= deadline]
        if any(item["mid"] is not None and not matches(item["mid"]) for item in following):
            continue
        after = next((item for item in ticks[index:] if item["time"] >= deadline), None)
        if after is not None and matches(after["mid"]):
            return when
    return None


def _vwap(levels_json: str, side: str, quantity: float) -> tuple[float | None, float]:
    levels = json.loads(levels_json).get(side, [])
    remaining = quantity
    cost = 0.0
    filled = 0.0
    for level in levels:
        size = min(remaining, float(level["size"]))
        cost += size * float(level["price"])
        filled += size
        remaining -= size
        if remaining <= 1e-9:
            break
    return (cost / filled if remaining <= 1e-9 and filled else None, filled)


def _event_market_analysis(
    store: WeatherStore,
    config: CityConfig,
    location: LocationInfo,
    contract: sqlite3.Row,
    contract_bin: sqlite3.Row,
    *,
    event_type: str,
    source: str,
    temperature_f: float,
    contract_temperature: int,
    object_time: datetime,
    known_at: datetime,
    metadata: dict[str, Any],
    quantity: float,
    thresholds: tuple[float, ...],
) -> dict[str, Any]:
    raw_ticks = store.connection.execute(
        """
        SELECT * FROM market_top_ticks WHERE token_id=? AND source='clob_ws'
          AND exchange_event_at BETWEEN ? AND ?
        ORDER BY exchange_event_at,tick_id
        """,
        (
            contract_bin["yes_token_id"],
            (object_time - timedelta(minutes=10)).isoformat(),
            (known_at + timedelta(hours=3)).isoformat(),
        ),
    ).fetchall()
    ticks = [
        {**dict(item), "time": _parse(str(item["exchange_event_at"]))}
        for item in raw_ticks
    ]
    pre = next((item for item in reversed(ticks) if item["time"] < object_time), None)
    first_move = next(
        (
            item
            for item in ticks
            if item["time"] >= object_time
            and pre is not None
            and (item["mid"], item["best_bid"], item["best_ask"])
            != (pre["mid"], pre["best_bid"], pre["best_ask"])
        ),
        None,
    )
    rising = event_type != "bin_eliminated"
    threshold_times = {
        str(threshold): (
            crossing.isoformat()
            if (
                crossing := _persistent_threshold(
                    ticks,
                    threshold if rising else 1 - threshold,
                    known_at,
                    rising=rising,
                )
            )
            else None
        )
        for threshold in thresholds
    }
    quote = store.connection.execute(
        """
        SELECT * FROM execution_quotes WHERE token_id=? AND received_at>=?
        ORDER BY received_at LIMIT 1
        """,
        (contract_bin["yes_token_id"], known_at.isoformat()),
    ).fetchone()
    final_label = store.connection.execute(
        """
        SELECT official_tmax_f FROM weather_daily_labels
        WHERE station_id=? AND local_date=? ORDER BY finalized_at DESC LIMIT 1
        """,
        (config.station_id, contract["local_day"]),
    ).fetchone()
    final_temperature = (
        _contract_temperature(float(final_label["official_tmax_f"])) if final_label else None
    )
    forecast = store.connection.execute(
        """
        SELECT MAX(p.temperature_f) AS remaining_tmax_f
        FROM weather_forecasts f JOIN forecast_points p USING(capture_id)
        WHERE f.station_id=? AND f.received_at<=? AND p.valid_at>=?
          AND COALESCE(p.object_local_date,substr(p.valid_at,1,10))=?
        GROUP BY f.capture_id ORDER BY f.received_at DESC LIMIT 1
        """,
        (
            config.station_id,
            known_at.isoformat(),
            object_time.isoformat(),
            contract["local_day"],
        ),
    ).fetchone()
    local_object_time = object_time.astimezone(config.zone)
    solar = sun(location.observer, date=local_object_time.date(), tzinfo=config.zone)
    ask_vwap, ask_depth = (
        _vwap(str(quote["top_levels_json"]), "asks", quantity)
        if quote
        else (None, 0.0)
    )
    best_ask = float(quote["best_ask"]) if quote and quote["best_ask"] is not None else None
    fee_rate = float(contract_bin["fee_rate"])
    fee_exponent = float(contract_bin["fee_exponent"])
    estimated_fee = (
        quantity * taker_fee_per_share(ask_vwap, fee_rate, fee_exponent)
        if ask_vwap is not None
        else None
    )
    final_bin = _matching_bin(
        store.connection.execute(
            "SELECT * FROM contract_bins WHERE contract_version_id=? ORDER BY ordinal",
            (contract["contract_version_id"],),
        ).fetchall(),
        final_temperature,
    ) if final_temperature is not None else None
    fills = store.connection.execute(
        """
        SELECT f.quantity,f.price,f.side,f.fee FROM paper_fills f
        JOIN decisions d USING(decision_id)
        JOIN contract_versions c USING(contract_version_id)
        WHERE c.event_id=? AND f.bin_id=?
        """,
        (contract["event_id"], contract_bin["bin_id"]),
    ).fetchall()
    paper_pnl = None
    if final_bin is not None and fills:
        won = final_bin["bin_id"] == contract_bin["bin_id"]
        net_shares = sum(
            float(fill["quantity"]) * (1 if fill["side"] == "buy" else -1)
            for fill in fills
        )
        cash_flow = sum(
            float(fill["quantity"])
            * (-float(fill["price"]) if fill["side"] == "buy" else float(fill["price"]))
            - float(fill["fee"])
            for fill in fills
        )
        paper_pnl = cash_flow + (net_shares if won else 0.0)
    return {
        "type": event_type,
        "event_id": contract["event_id"],
        "local_day": contract["local_day"],
        "bin_id": contract_bin["bin_id"],
        "label": contract_bin["label"],
        "source": source,
        "temperature_f": temperature_f,
        "contract_temperature_f": contract_temperature,
        "object_time": object_time.isoformat(),
        "system_received_at": known_at.isoformat(),
        "source_latency_seconds": (known_at - object_time).total_seconds(),
        "first_market_move_at": first_move["time"].isoformat() if first_move else None,
        "tradable_lead_seconds": (
            (first_move["time"] - known_at).total_seconds() if first_move else None
        ),
        "pre_mid": pre["mid"] if pre else None,
        "pre_best_bid": pre["best_bid"] if pre else None,
        "pre_best_ask": pre["best_ask"] if pre else None,
        "target_quantity": quantity,
        "target_ask_vwap": ask_vwap,
        "executable_ask_depth": ask_depth,
        "slippage": ask_vwap - best_ask if ask_vwap is not None and best_ask is not None else None,
        "estimated_fee": estimated_fee,
        "paper_pnl": paper_pnl,
        "threshold_times": threshold_times,
        "sunset": solar["sunset"].astimezone(UTC).isoformat(),
        "minutes_to_sunset": (solar["sunset"] - local_object_time).total_seconds() / 60,
        "solar_elevation": elevation(location.observer, local_object_time),
        "remaining_forecast_tmax_f": (
            float(forecast["remaining_tmax_f"])
            if forecast and forecast["remaining_tmax_f"] is not None
            else None
        ),
        "weather_covariates": metadata,
        "final_official_temperature_f": (
            float(final_label["official_tmax_f"]) if final_label else None
        ),
        "final_bin_id": final_bin["bin_id"] if final_bin else None,
        "higher_bin_later": (
            final_temperature > contract_temperature if final_temperature is not None else None
        ),
    }


def tmax_repricing_report(
    database_path: str | Path,
    config: CityConfig,
    *,
    start_date: date,
    end_date: date,
    quantity: float,
    thresholds: tuple[float, ...],
) -> dict[str, Any]:
    location = LocationInfo(
        config.city_name,
        "US",
        config.timezone,
        config.latitude,
        config.longitude,
    )
    events: list[dict[str, Any]] = []
    with WeatherStore(database_path, read_only=True) as store:
        contracts = store.connection.execute(
            """
            SELECT * FROM contract_versions
            WHERE local_day BETWEEN ? AND ?
            ORDER BY local_day,received_at DESC
            """,
            (start_date.isoformat(), end_date.isoformat()),
        ).fetchall()
        selected: dict[str, sqlite3.Row] = {}
        for contract in contracts:
            selected.setdefault(str(contract["local_day"]), contract)
        for local_day, contract in selected.items():
            bins = store.connection.execute(
                "SELECT * FROM contract_bins WHERE contract_version_id=? ORDER BY ordinal",
                (contract["contract_version_id"],),
            ).fetchall()
            observations = store.connection.execute(
                """
                SELECT observed_at,received_at,temperature_f,source,weather_metadata_json
                FROM weather_observations
                WHERE station_id=? AND COALESCE(object_local_date,local_date)=?
                ORDER BY received_at,observed_at
                """,
                (config.station_id, local_day),
            ).fetchall()
            running = -math.inf
            seen_bins: set[str] = set()
            eliminated_bins: set[str] = set()
            for observation in observations:
                contract_value = _contract_temperature(float(observation["temperature_f"]))
                if contract_value <= running:
                    continue
                running = contract_value
                object_time = _parse(str(observation["observed_at"]))
                known_at = _parse(str(observation["received_at"]))
                metadata = json.loads(str(observation["weather_metadata_json"] or "{}"))
                target_bin = _matching_bin(bins, contract_value)
                if target_bin is not None and str(target_bin["bin_id"]) not in seen_bins:
                    seen_bins.add(str(target_bin["bin_id"]))
                    events.append(
                        _event_market_analysis(
                            store,
                            config,
                            location,
                            contract,
                            target_bin,
                            event_type="bin_entered",
                            source=str(observation["source"]),
                            temperature_f=float(observation["temperature_f"]),
                            contract_temperature=contract_value,
                            object_time=object_time,
                            known_at=known_at,
                            metadata=metadata,
                            quantity=quantity,
                            thresholds=thresholds,
                        )
                    )
                for low_bin in bins:
                    high = low_bin["upper_bound"]
                    bin_id = str(low_bin["bin_id"])
                    if high is None or contract_value <= float(high) or bin_id in eliminated_bins:
                        continue
                    eliminated_bins.add(bin_id)
                    events.append(
                        _event_market_analysis(
                            store,
                            config,
                            location,
                            contract,
                            low_bin,
                            event_type="bin_eliminated",
                            source=str(observation["source"]),
                            temperature_f=float(observation["temperature_f"]),
                            contract_temperature=contract_value,
                            object_time=object_time,
                            known_at=known_at,
                            metadata=metadata,
                            quantity=quantity,
                            thresholds=thresholds,
                        )
                    )
            forecast_rows = store.connection.execute(
                """
                SELECT f.capture_id,f.issued_at,f.received_at,
                       MAX(p.temperature_f) AS forecast_tmax_f,
                       GROUP_CONCAT(p.valid_at || ':' || p.temperature_f,'|') AS path
                FROM weather_forecasts f JOIN forecast_points p USING(capture_id)
                WHERE f.station_id=?
                  AND COALESCE(p.object_local_date,substr(p.valid_at,1,10))=?
                GROUP BY f.capture_id,f.issued_at,f.received_at
                ORDER BY f.received_at
                """,
                (config.station_id, local_day),
            ).fetchall()
            previous_forecast: tuple[float, str] | None = None
            for forecast in forecast_rows:
                forecast_tmax = float(forecast["forecast_tmax_f"])
                current_forecast = (forecast_tmax, str(forecast["path"]))
                if previous_forecast is not None and current_forecast != previous_forecast:
                    contract_value = _contract_temperature(forecast_tmax)
                    forecast_bin = _matching_bin(bins, contract_value)
                    if forecast_bin is not None:
                        events.append(
                            _event_market_analysis(
                                store,
                                config,
                                location,
                                contract,
                                forecast_bin,
                                event_type="forecast_revised",
                                source="nws_forecast",
                                temperature_f=forecast_tmax,
                                contract_temperature=contract_value,
                                object_time=_parse(str(forecast["issued_at"])),
                                known_at=_parse(str(forecast["received_at"])),
                                metadata={"forecast_path": forecast["path"]},
                                quantity=quantity,
                                thresholds=thresholds,
                            )
                        )
                previous_forecast = current_forecast
    events.sort(key=lambda item: (item["object_time"], item["type"], item["bin_id"]))
    return {
        "station_id": config.station_id,
        "object_timezone": config.timezone,
        "from": start_date.isoformat(),
        "to": end_date.isoformat(),
        "quantity": quantity,
        "thresholds": list(thresholds),
        "events": events,
    }

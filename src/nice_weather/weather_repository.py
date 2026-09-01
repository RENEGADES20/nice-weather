from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from nice_weather.domain import ForecastPoint, WeatherObservation


@dataclass(frozen=True)
class CapturedWeatherState:
    station_id: str
    local_date: date
    decision_time: datetime
    observations: tuple[WeatherObservation, ...]
    forecasts: tuple[ForecastPoint, ...]
    settlement: dict[str, Any] | None
    input_capture_ids: tuple[str, ...]


class WeatherRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()

    def get_state_as_of(
        self, station_id: str, local_date: date, decision_time: datetime
    ) -> CapturedWeatherState:
        uri = f"file:{self.database_path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
            connection.row_factory = sqlite3.Row
            observations = connection.execute(
                """
                SELECT observation.* FROM weather_observations AS observation
                JOIN (
                  SELECT source, observed_at, MAX(received_at) AS received_at
                  FROM weather_observations
                  WHERE station_id=? AND local_date=? AND received_at<=?
                  GROUP BY source, observed_at
                ) AS latest
                ON latest.source=observation.source
                  AND latest.observed_at=observation.observed_at
                  AND latest.received_at=observation.received_at
                WHERE observation.station_id=?
                ORDER BY observation.observed_at
                """,
                (station_id, local_date.isoformat(), decision_time.isoformat(), station_id),
            ).fetchall()
            forecast_capture = connection.execute(
                """
                SELECT capture_id FROM weather_forecasts
                WHERE station_id=? AND received_at<=?
                ORDER BY received_at DESC LIMIT 1
                """,
                (station_id, decision_time.isoformat()),
            ).fetchone()
            forecast_rows = []
            if forecast_capture is not None:
                forecast_rows = connection.execute(
                    """
                    SELECT * FROM forecast_points
                    WHERE snapshot_id=? AND received_at<=? ORDER BY valid_at
                    """,
                    (forecast_capture["capture_id"], decision_time.isoformat()),
                ).fetchall()
            settlement_row = connection.execute(
                """
                SELECT * FROM settlement_evidence
                WHERE station_id=? AND local_date=? AND received_at<=?
                ORDER BY received_at DESC LIMIT 1
                """,
                (station_id, local_date.isoformat(), decision_time.isoformat()),
            ).fetchone()

        normalized_observations = tuple(
            WeatherObservation(
                snapshot_id=str(row["snapshot_id"]),
                station_id=str(row["station_id"]),
                observed_at=datetime.fromisoformat(row["observed_at"]),
                received_at=datetime.fromisoformat(row["received_at"]),
                temperature_f=float(row["temperature_f"]),
                raw_text=str(row["raw_text"]),
                source=str(row["source"]),
                metadata=json.loads(row["weather_metadata_json"] or "{}"),
            )
            for row in observations
        )
        forecasts = tuple(
            ForecastPoint(
                snapshot_id=str(row["snapshot_id"]),
                source=str(row["source"]),
                issued_at=datetime.fromisoformat(row["issued_at"]),
                valid_at=datetime.fromisoformat(row["valid_at"]),
                received_at=datetime.fromisoformat(row["received_at"]),
                temperature_f=float(row["temperature_f"]),
            )
            for row in forecast_rows
        )
        settlement = dict(settlement_row) if settlement_row is not None else None
        capture_ids = {
            *(item.snapshot_id for item in normalized_observations),
            *(item.snapshot_id for item in forecasts),
        }
        if settlement is not None:
            capture_ids.add(str(settlement["capture_id"]))
        return CapturedWeatherState(
            station_id=station_id,
            local_date=local_date,
            decision_time=decision_time,
            observations=normalized_observations,
            forecasts=forecasts,
            settlement=settlement,
            input_capture_ids=tuple(sorted(capture_ids)),
        )


class OfflineWeatherRepository(WeatherRepository):
    """Local materialization used by replay jobs after importing R2 v1/v2 manifests."""

    @staticmethod
    def supported_manifest_versions() -> tuple[int, ...]:
        return (1, 2)

    @staticmethod
    def normalize_manifest(payload: str | bytes) -> dict[str, Any]:
        manifest = json.loads(payload)
        version = int(manifest.get("schema_version", 1))
        if version not in OfflineWeatherRepository.supported_manifest_versions():
            raise ValueError(f"Unsupported R2 manifest schema version: {version}")
        return manifest

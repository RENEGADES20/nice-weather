from __future__ import annotations

from datetime import date, datetime

import httpx

from nice_weather.config import CityConfig
from nice_weather.domain import RawSnapshot, content_hash, stable_id, utc_now


class WeatherReadOnlyAdapter:
    awc_url = "https://aviationweather.gov/api/data/metar"
    nws_url = "https://api.weather.gov"

    def __init__(self, timeout: float = 15.0) -> None:
        headers = {"User-Agent": "nice-weather/0.1 (github.com/RENEGADES20/nice-weather)"}
        self.client = httpx.Client(timeout=timeout, follow_redirects=True, headers=headers)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> WeatherReadOnlyAdapter:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def fetch_observations(
        self, station_id: str, start: datetime, decision_time: datetime
    ) -> RawSnapshot:
        hours = max(2, min(48, int((decision_time - start).total_seconds() / 3600) + 2))
        response = self.client.get(
            self.awc_url,
            params={"ids": station_id, "format": "json", "taf": "false", "hours": hours},
        )
        payload = [] if response.status_code == 204 else response.json()
        response.raise_for_status()
        payload_hash = content_hash(payload)
        received_at = utc_now()
        return RawSnapshot(
            snapshot_id=stable_id("snapshot", "aviationweather", station_id, payload_hash),
            source="aviationweather",
            kind="metar",
            received_at=received_at,
            source_version=payload_hash,
            payload={"observations": payload},
            request_url=str(response.request.url),
            http_status=response.status_code,
        )

    def fetch_forecast(
        self, config: CityConfig, local_day: date, decision_time: datetime
    ) -> list[RawSnapshot]:
        points_response = self.client.get(
            f"{self.nws_url}/points/{config.latitude:.7f},{config.longitude:.7f}"
        )
        points_response.raise_for_status()
        points = points_response.json()
        hourly_response = self.client.get(points["properties"]["forecastHourly"])
        hourly_response.raise_for_status()
        hourly = hourly_response.json()
        snapshots: list[RawSnapshot] = []
        for kind, response, payload in (
            ("points", points_response, points),
            ("hourly_forecast", hourly_response, hourly),
        ):
            payload_hash = content_hash(payload)
            received_at = utc_now()
            snapshots.append(
                RawSnapshot(
                    snapshot_id=stable_id("snapshot", "nws", kind, payload_hash),
                    source="nws",
                    kind=kind,
                    received_at=received_at,
                    source_version=payload_hash,
                    payload=payload,
                    request_url=str(response.url),
                    http_status=response.status_code,
                )
            )
        return snapshots

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

from nice_weather.config import CityConfig
from nice_weather.domain import RawSnapshot


class MarketDataAdapter(Protocol):
    def discover(self, config: CityConfig, decision_time: datetime) -> RawSnapshot: ...

    def fetch_books(self, token_ids: list[str], decision_time: datetime) -> list[RawSnapshot]: ...


class ObservationAdapter(Protocol):
    def fetch_observations(
        self, station_id: str, start: datetime, decision_time: datetime
    ) -> RawSnapshot: ...


class ForecastAdapter(Protocol):
    def fetch_forecast(
        self, config: CityConfig, local_day: date, decision_time: datetime
    ) -> list[RawSnapshot]: ...

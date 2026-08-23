from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class FreshnessConfig:
    market_metadata_seconds: int
    order_book_seconds: int
    observation_age_seconds: int
    observation_receipt_seconds: int
    forecast_issue_seconds: int
    forecast_receipt_seconds: int
    runner_heartbeat_seconds: int


@dataclass(frozen=True)
class RunnerConfig:
    poll_interval_seconds: int
    metadata_interval_seconds: int
    observation_interval_seconds: int
    forecast_interval_seconds: int
    dashboard_refresh_seconds: int


@dataclass(frozen=True)
class ModelConfig:
    version: str
    sigma_f: float
    uncertainty_interval: float


@dataclass(frozen=True)
class SignalConfig:
    uncertainty_buffer: float
    minimum_net_edge: float
    target_notional: float


@dataclass(frozen=True)
class PaperConfig:
    starting_cash: float
    max_bin_notional: float
    max_city_day_notional: float
    stale_order_cycles: int


@dataclass(frozen=True)
class CityConfig:
    city_code: str
    city_name: str
    station_id: str
    station_name: str
    latitude: float
    longitude: float
    timezone: str
    metric: str
    allowed_units: tuple[str, ...]
    freshness: FreshnessConfig
    runner: RunnerConfig
    model: ModelConfig
    signal: SignalConfig
    paper: PaperConfig

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


def default_config_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "nyc_klga.toml"


def load_city_config(path: str | Path | None = None) -> CityConfig:
    config_path = Path(path) if path else default_config_path()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)
    city = raw["city"]
    config = CityConfig(
        city_code=city["city_code"],
        city_name=city["city_name"],
        station_id=city["station_id"],
        station_name=city["station_name"],
        latitude=float(city["latitude"]),
        longitude=float(city["longitude"]),
        timezone=city["timezone"],
        metric=city["metric"],
        allowed_units=tuple(city["allowed_units"]),
        freshness=FreshnessConfig(**raw["freshness"]),
        runner=RunnerConfig(**raw["runner"]),
        model=ModelConfig(**raw["model"]),
        signal=SignalConfig(**raw["signal"]),
        paper=PaperConfig(**raw["paper"]),
    )
    validate_city_config(config)
    return config


def validate_city_config(config: CityConfig) -> None:
    if config.city_code != "NYC" or config.station_id != "KLGA":
        raise ValueError("MVP configuration must map NYC to KLGA")
    if config.metric != "daily_max_temperature":
        raise ValueError("MVP supports daily_max_temperature only")
    if not config.allowed_units or set(config.allowed_units) - {"F"}:
        raise ValueError("NYC MVP currently accepts Fahrenheit contracts only")
    if not -90 <= config.latitude <= 90 or not -180 <= config.longitude <= 180:
        raise ValueError("Station coordinates are invalid")
    _ = config.zone
    positive_values = (
        config.freshness.market_metadata_seconds,
        config.freshness.order_book_seconds,
        config.runner.poll_interval_seconds,
        config.model.sigma_f,
        config.paper.starting_cash,
    )
    if any(value <= 0 for value in positive_values):
        raise ValueError("Freshness, runner, model and paper limits must be positive")

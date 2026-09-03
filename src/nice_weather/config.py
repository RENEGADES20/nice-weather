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
class CollectorConfig:
    metar_interval_seconds: int
    forecast_interval_seconds: int
    nws_observation_interval_seconds: int
    settlement_interval_seconds: int
    settlement_close_interval_seconds: int
    r2_sync_interval_seconds: int
    daily_export_hour: int
    daily_export_minute: int
    storage_warning_bytes: int
    settlement_url: str
    nws_observation_overlap_hours: int = 2
    metar_active_interval_seconds: int = 30
    metar_active_start_hour: int = 6
    metar_active_end_hour: int = 23
    market_discovery_interval_seconds: int = 300
    market_stream_reconnect_seconds: int = 5


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
    quote_probability_floor: float = 0.02


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
    collector: CollectorConfig
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
        collector=CollectorConfig(**raw["collector"]),
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
        config.collector.metar_interval_seconds,
        config.collector.metar_active_interval_seconds,
        config.collector.forecast_interval_seconds,
        config.collector.nws_observation_interval_seconds,
        config.collector.nws_observation_overlap_hours,
        config.collector.settlement_interval_seconds,
        config.collector.settlement_close_interval_seconds,
        config.collector.r2_sync_interval_seconds,
        config.collector.storage_warning_bytes,
        config.collector.market_discovery_interval_seconds,
        config.collector.market_stream_reconnect_seconds,
        config.model.sigma_f,
        config.paper.starting_cash,
    )
    if any(value <= 0 for value in positive_values):
        raise ValueError("Freshness, runner, model and paper limits must be positive")
    if not 0 <= config.signal.quote_probability_floor <= 1:
        raise ValueError("quote_probability_floor must be between 0 and 1")
    if not 0 <= config.collector.daily_export_hour <= 23:
        raise ValueError("Collector daily export hour must be between 0 and 23")
    if not 0 <= config.collector.daily_export_minute <= 59:
        raise ValueError("Collector daily export minute must be between 0 and 59")
    if not 0 <= config.collector.metar_active_start_hour <= 23:
        raise ValueError("METAR active start hour must be between 0 and 23")
    if not 1 <= config.collector.metar_active_end_hour <= 24:
        raise ValueError("METAR active end hour must be between 1 and 24")
    if not config.collector.settlement_url.startswith("https://www.weather.gov/"):
        raise ValueError("Settlement evidence URL must use the official weather.gov site")

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from nice_weather.reason_codes import ReasonCode


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def content_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class RunMode(StrEnum):
    FIXTURE = "FIXTURE"
    SHADOW = "SHADOW"
    PAPER = "PAPER"


class HealthLevel(StrEnum):
    OK = "OK"
    WARN = "WARN"
    BLOCKED = "BLOCKED"


class SignalAction(StrEnum):
    BUY_YES = "BUY_YES"
    EXIT_YES = "EXIT_YES"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"


class PaperOrderStatus(StrEnum):
    SUBMITTED = "submitted"
    ACCEPTED = "accepted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


@dataclass(frozen=True)
class RawSnapshot:
    snapshot_id: str
    source: str
    kind: str
    received_at: datetime
    source_version: str
    payload: Any
    source_time: datetime | None = None
    observed_at: datetime | None = None
    issued_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    event_id: str | None = None
    market_id: str | None = None
    token_id: str | None = None
    request_url: str | None = None
    http_status: int | None = None

    def __post_init__(self) -> None:
        require_aware(self.received_at, "received_at")
        for name in ("source_time", "observed_at", "issued_at", "valid_from", "valid_to"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)

    @property
    def hash(self) -> str:
        return content_hash(self.payload)

    def available_at(self, decision_time: datetime) -> bool:
        require_aware(decision_time, "decision_time")
        return self.received_at <= decision_time


@dataclass(frozen=True)
class SourceCapture:
    capture_id: str
    source: str
    kind: str
    station_id: str
    requested_at: datetime
    received_at: datetime
    local_date: date
    source_version: str
    content_hash: str
    request_url: str
    http_status: int
    content_type: str
    raw_bytes: bytes
    source_time: datetime | None = None
    observed_at: datetime | None = None
    issued_at: datetime | None = None
    content_encoding: str = "gzip"

    def __post_init__(self) -> None:
        for name in ("requested_at", "received_at"):
            require_aware(getattr(self, name), name)
        for name in ("source_time", "observed_at", "issued_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)


@dataclass(frozen=True)
class SettlementEvidence:
    evidence_id: str
    capture_id: str
    station_id: str
    local_date: date
    received_at: datetime
    table_text: str
    parse_status: str
    tmax_f: float | None = None
    page_updated_at: datetime | None = None
    first_next_day_observed_at: datetime | None = None
    first_next_day_temperature_f: float | None = None
    no_trade_reason: str | None = None
    finalized: bool = False
    screenshot_png: bytes | None = None

    def __post_init__(self) -> None:
        require_aware(self.received_at, "received_at")
        for name in ("page_updated_at", "first_next_day_observed_at"):
            value = getattr(self, name)
            if value is not None:
                require_aware(value, name)


@dataclass(frozen=True)
class ContractBin:
    bin_id: str
    label: str
    ordinal: int
    market_id: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    lower_bound: float | None
    upper_bound: float | None
    lower_inclusive: bool = True
    upper_inclusive: bool = True
    active: bool = True
    closed: bool = False
    accepting_orders: bool = True
    tick_size: float = 0.01
    minimum_order_size: float = 1.0
    fee_rate: float = 0.0
    fee_exponent: float = 2.0


@dataclass(frozen=True)
class MarketContract:
    contract_version_id: str
    event_id: str
    event_slug: str
    event_title: str
    market_url: str
    local_day: date
    city_code: str
    station_id: str
    timezone: str
    metric: str
    unit: str
    rounding: str
    observation_start: datetime
    observation_end: datetime
    settlement_source: str
    rule_text: str
    rule_version: str
    rule_hash: str
    parse_status: str
    ambiguities: tuple[ReasonCode, ...] = ()
    event_active: bool = True
    event_closed: bool = False
    bins: tuple[ContractBin, ...] = ()

    @property
    def tradable(self) -> bool:
        return self.parse_status == "parsed" and not self.ambiguities and not self.event_closed


@dataclass(frozen=True)
class PriceLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    snapshot_id: str
    book_hash: str
    token_id: str
    market_id: str
    exchange_time: datetime
    received_at: datetime
    bids: tuple[PriceLevel, ...]
    asks: tuple[PriceLevel, ...]

    @property
    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None


@dataclass(frozen=True)
class WeatherObservation:
    snapshot_id: str
    station_id: str
    observed_at: datetime
    received_at: datetime
    temperature_f: float
    raw_text: str = ""


@dataclass(frozen=True)
class ForecastPoint:
    snapshot_id: str
    source: str
    issued_at: datetime
    valid_at: datetime
    received_at: datetime
    temperature_f: float


@dataclass(frozen=True)
class HealthCheck:
    source: str
    level: HealthLevel
    received_at: datetime | None
    source_time: datetime | None
    age_seconds: float | None
    reason_codes: tuple[ReasonCode, ...] = ()
    duplicate_count: int = 0
    out_of_order_count: int = 0
    gap_count: int = 0
    message: str = ""


@dataclass(frozen=True)
class DataHealth:
    level: HealthLevel
    checks: tuple[HealthCheck, ...]
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True)
class UnifiedState:
    decision_time: datetime
    mode: RunMode
    contract: MarketContract
    input_snapshot_ids: tuple[str, ...]
    order_books: dict[str, OrderBook]
    observations: tuple[WeatherObservation, ...]
    forecasts: tuple[ForecastPoint, ...]
    health: DataHealth
    input_set_hash: str


@dataclass(frozen=True)
class BinProbability:
    bin_id: str
    probability: float


@dataclass(frozen=True)
class ProbabilityEstimate:
    model_version: str
    generated_at: datetime
    baseline_tmax_f: float
    observed_tmax_f: float | None
    mean_tmax_f: float
    median_tmax_f: float
    interval_low_f: float
    interval_high_f: float
    probabilities: tuple[BinProbability, ...]
    probability_sum: float
    input_snapshot_ids: tuple[str, ...]


@dataclass(frozen=True)
class DecisionOutcome:
    decision_id: str
    bin_id: str
    label: str
    model_probability: float
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    executable_quantity: float
    executable_price: float | None
    executable_depth: float
    gross_edge: float | None
    fee: float
    slippage: float
    uncertainty_buffer: float
    net_edge: float | None
    action: SignalAction
    risk_approved: bool
    reason_codes: tuple[ReasonCode, ...] = ()
    paper_position: float = 0.0


@dataclass(frozen=True)
class Decision:
    decision_id: str
    decision_time: datetime
    mode: RunMode
    contract_version_id: str
    input_set_hash: str
    model_version: str
    status: str
    overall_action: str
    health_level: HealthLevel
    reason_codes: tuple[ReasonCode, ...]
    outcomes: tuple[DecisionOutcome, ...] = field(default_factory=tuple)

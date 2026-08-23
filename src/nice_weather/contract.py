from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from nice_weather.config import CityConfig
from nice_weather.domain import ContractBin, MarketContract, content_hash, stable_id
from nice_weather.reason_codes import ReasonCode

_DAY_RE = re.compile(r"on\s+(\d{1,2})\s+([A-Za-z]{3})\s+'(\d{2})", re.IGNORECASE)
_RANGE_RE = re.compile(r"^(\d+)\s*-\s*(\d+)°F$")
_LOW_RE = re.compile(r"^(\d+)°F or below$")
_HIGH_RE = re.compile(r"^(\d+)°F or higher$")
_MONTHS = {
    month: index
    for index, month in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}


def _parse_day(text: str) -> date | None:
    match = _DAY_RE.search(text)
    if not match or match.group(2).title() not in _MONTHS:
        return None
    return date(2000 + int(match.group(3)), _MONTHS[match.group(2).title()], int(match.group(1)))


def _parse_bounds(label: str) -> tuple[float | None, float | None] | None:
    if match := _LOW_RE.match(label):
        return None, float(match.group(1))
    if match := _HIGH_RE.match(label):
        return float(match.group(1)), None
    if match := _RANGE_RE.match(label):
        return float(match.group(1)), float(match.group(2))
    return None


def parse_gamma_contract(payload: dict[str, Any], config: CityConfig) -> MarketContract:
    events = payload.get("events", payload if isinstance(payload, list) else [])
    if len(events) != 1:
        raise ValueError(f"Expected one Gamma event, received {len(events)}")
    event = events[0]
    description = str(event.get("description", ""))
    ambiguities: list[ReasonCode] = []
    local_day = _parse_day(description)
    if local_day is None:
        ambiguities.append(ReasonCode.RULE_DATE_AMBIGUOUS)
        local_day = date.fromisoformat(str(event.get("endDate", "1970-01-01"))[:10])
    description_lower = description.lower()
    if "laguardia airport station" not in description_lower or "klga" not in description_lower:
        ambiguities.append(ReasonCode.RULE_STATION_AMBIGUOUS)
    if "degrees Fahrenheit" not in description or "whole degrees Fahrenheit" not in description:
        ambiguities.append(ReasonCode.RULE_UNIT_AMBIGUOUS)
    settlement_source = str(event.get("resolutionSource", ""))
    if not settlement_source or not any(
        host in settlement_source for host in ("weather.gov", "wunderground.com")
    ):
        ambiguities.append(ReasonCode.RULE_SETTLEMENT_SOURCE_AMBIGUOUS)
    if "first data point for the following date" not in description:
        ambiguities.append(ReasonCode.RULE_OBSERVATION_WINDOW_AMBIGUOUS)

    bins: list[ContractBin] = []
    for ordinal, market in enumerate(event.get("markets", [])):
        label = str(market.get("groupItemTitle", ""))
        bounds = _parse_bounds(label)
        if bounds is None:
            ambiguities.append(ReasonCode.RULE_BIN_AMBIGUOUS)
            continue
        try:
            outcomes = json.loads(market["outcomes"])
            tokens = json.loads(market["clobTokenIds"])
        except (KeyError, TypeError, json.JSONDecodeError):
            outcomes, tokens = [], []
        if outcomes != ["Yes", "No"] or len(tokens) != 2:
            ambiguities.append(ReasonCode.MARKET_TOKEN_MAPPING_INVALID)
            continue
        fee = market.get("feeSchedule") or {}
        bins.append(
            ContractBin(
                bin_id=stable_id("bin", event["id"], market["id"], label),
                label=label,
                ordinal=ordinal,
                market_id=str(market["id"]),
                condition_id=str(market["conditionId"]),
                yes_token_id=str(tokens[0]),
                no_token_id=str(tokens[1]),
                lower_bound=bounds[0],
                upper_bound=bounds[1],
                active=bool(market.get("active", False)),
                closed=bool(market.get("closed", False)),
                accepting_orders=bool(market.get("acceptingOrders", False)),
                tick_size=float(market.get("orderPriceMinTickSize", 0.01)),
                minimum_order_size=float(market.get("orderMinSize", 1.0)),
                fee_rate=float(fee.get("rate", 0.0)),
                fee_exponent=float(fee.get("exponent", 1.0)),
            )
        )
    if not _valid_partition(bins):
        ambiguities.append(ReasonCode.RULE_BIN_AMBIGUOUS)
    if not event.get("active") or event.get("closed"):
        ambiguities.append(ReasonCode.MARKET_CLOSED)

    zone = ZoneInfo(config.timezone)
    observation_start = datetime.combine(local_day, time.min, tzinfo=zone)
    observation_end = observation_start + timedelta(days=1)
    rule_hash = content_hash({"description": description, "markets": event.get("markets", [])})
    rule_version = "/".join(
        sorted({str(m.get("version", "unknown")) for m in event.get("markets", [])})
    )
    return MarketContract(
        contract_version_id=stable_id("contract", event["id"], rule_hash),
        event_id=str(event["id"]),
        event_slug=str(event["slug"]),
        event_title=str(event["title"]),
        market_url=f"https://polymarket.com/event/{event['slug']}",
        local_day=local_day,
        city_code=config.city_code,
        station_id=config.station_id,
        timezone=config.timezone,
        metric=config.metric,
        unit="F",
        rounding="source_whole_degree",
        observation_start=observation_start.astimezone(UTC),
        observation_end=observation_end.astimezone(UTC),
        settlement_source=settlement_source,
        rule_text=description,
        rule_version=rule_version,
        rule_hash=rule_hash,
        parse_status="parsed" if not ambiguities else "ambiguous",
        ambiguities=tuple(dict.fromkeys(ambiguities)),
        event_active=bool(event.get("active")),
        event_closed=bool(event.get("closed")),
        bins=tuple(bins),
    )


def _valid_partition(bins: list[ContractBin]) -> bool:
    if len(bins) < 2 or bins[0].lower_bound is not None or bins[-1].upper_bound is not None:
        return False
    for previous, current in zip(bins, bins[1:], strict=False):
        if previous.upper_bound is None or current.lower_bound is None:
            return False
        if current.lower_bound != previous.upper_bound + 1:
            return False
    return True

from __future__ import annotations

import argparse
import logging
import math
import os
import uuid
from bisect import bisect_left
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from functools import lru_cache
from html import escape
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qsl, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nice_weather.config import load_city_config
from nice_weather.domain import content_hash
from nice_weather.queries import DashboardQuery
from nice_weather.trading_chart import trading_chart, trading_chart_feed

logger = logging.getLogger(__name__)

_TIMESTAMP_KEYS = frozenset({"decision_time", "source_time", "valid_from", "valid_to"})
_NEW_YORK = ZoneInfo("America/New_York")
_SOURCE_SPECS = {
    "forecast": {
        "name": "NWS Hourly Forecast",
        "color": "#60A5FA",
        "kind": "forecast",
        "data_type": "hourly forecast temperature",
        "purpose": "expected intraday temperature trajectory",
        "description": "Hourly forecast temperatures keyed by forecast valid time.",
    },
    "weather-gov": {
        "name": "Weather.gov Hourly Data",
        "color": "#F59E0B",
        "kind": "step-day",
        "data_type": "official hourly table and cumulative Tmax",
        "purpose": "settlement-evidence progress for the target market day",
        "description": "Official hourly table and cumulative Tmax for the target market day.",
    },
    "metar": {
        "name": "AviationWeather METAR",
        "color": "#E76F51",
        "kind": "step-fresh",
        "data_type": "airport METAR observation",
        "purpose": "fast operational station temperature updates",
        "description": "Airport METAR observations keyed by report observation time.",
    },
    "nws-observations": {
        "name": "NWS Station Observations",
        "color": "#98A2B3",
        "kind": "step-fresh",
        "data_type": "NWS station observation",
        "purpose": "independent official station-observation comparison",
        "description": "NWS station observations keyed by observation time.",
    },
}
_DEFAULT_SOURCES = ("forecast", "weather-gov", "metar")
_DEFAULT_DIFFERENCES = (
    "metar-minus-forecast",
    "price-minus-forecast",
    "price-minus-metar",
)
_DIFFERENCE_SPECS = (
    {
        "id": "metar-minus-forecast",
        "name": "METAR − Forecast",
        "leftId": "metar",
        "rightId": "forecast",
        "unit": "°F",
        "axis": "left",
        "color": "#C2412D",
    },
    {
        "id": "weather-gov-minus-forecast",
        "name": "Weather.gov Hourly Temp − Forecast",
        "leftId": "weather-gov",
        "rightId": "forecast",
        "unit": "°F",
        "axis": "left",
        "color": "#B45309",
    },
    {
        "id": "weather-gov-minus-metar",
        "name": "Weather.gov Hourly Temp − METAR",
        "leftId": "weather-gov",
        "rightId": "metar",
        "unit": "°F",
        "axis": "left",
        "color": "#7C3AED",
    },
    {
        "id": "price-minus-forecast",
        "name": "Price × 100 − Forecast",
        "leftId": "price",
        "rightId": "forecast",
        "unit": "display spread",
        "axis": "right",
        "color": "#1D4ED8",
    },
    {
        "id": "price-minus-metar",
        "name": "Price × 100 − METAR",
        "leftId": "price",
        "rightId": "metar",
        "unit": "display spread",
        "axis": "right",
        "color": "#0F766E",
    },
    {
        "id": "price-minus-weather-gov",
        "name": "Price × 100 − Weather.gov Hourly Temp",
        "leftId": "price",
        "rightId": "weather-gov",
        "unit": "display spread",
        "axis": "right",
        "color": "#9333EA",
    },
)

_DASHBOARD_STYLE = """
<style>
.dashboard-status {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 0.25rem 0 0.75rem;
  border-block: 1px solid #E2E7EE;
}
.dashboard-status__item {
  min-width: 0;
  padding: 0.65rem 0.75rem;
  border-right: 1px solid #E2E7EE;
}
.dashboard-status__item:nth-child(4n) { border-right: 0; }
.dashboard-status__label {
  display: block;
  margin-bottom: 0.25rem;
  color: #667085;
  font-size: 0.75rem;
}
.dashboard-status__value {
  display: block;
  overflow: hidden;
  color: #172033;
  font-size: 1.15rem;
  font-weight: 500;
  line-height: 1.25;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.weather-source-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 1px;
  margin: 0.35rem 0 0.75rem;
  overflow: hidden;
  background: #E2E7EE;
  border: 1px solid #E2E7EE;
  border-radius: 0.45rem;
}
.weather-source {
  min-width: 0;
  padding: 0.65rem 0.75rem;
  background: #FFFFFF;
}
.weather-source__label { color: #667085; font-size: 0.75rem; }
.weather-source__value { margin-top: 0.2rem; color: #172033; font-size: 1.05rem; }
.weather-source__info {
  display: inline-flex; align-items: center; justify-content: center;
  width: 1rem; height: 1rem; margin-left: 0.2rem;
  color: #667085; border: 1px solid #C8D0DC; border-radius: 50%;
  font-size: 0.68rem; font-style: normal; cursor: help;
}
.resolution-source { margin: 0.3rem 0 0.8rem; color: #667085; font-size: 0.86rem; }
div[data-testid="stMultiSelect"] [data-baseweb="tag"] {
  color: #1D4ED8 !important; background: #EAF2FF !important;
}
div[data-testid="stMultiSelect"] [data-baseweb="tag"] * {
  color: #1D4ED8 !important;
}
div[data-testid="stMultiSelect"] [data-tag] {
  color: #1D4ED8 !important; background-color: #EAF2FF !important;
}
div[data-testid="stMultiSelect"] [data-tag] * { color: #1D4ED8 !important; }
div[role="radiogroup"] input[type="radio"] { accent-color: #2563EB !important; }
div[role="radiogroup"] label:has(input:checked) {
  color: #1D4ED8 !important; background: #EAF2FF !important; border-radius: 0.35rem;
}
div[role="radiogroup"] label[data-selected="true"] > div {
  background-color: #EAF2FF !important; border-radius: 0.35rem;
}
div[role="radiogroup"] label[data-selected="true"] > div > div > div:first-child {
  background-color: #2563EB !important;
}
div[role="radiogroup"] label[data-selected="true"] p { color: #1D4ED8 !important; }
[data-testid="stTab"][role="tab"][aria-selected="true"] {
  color: #2563EB !important; border-bottom-color: #2563EB !important;
}
[data-testid="stTab"][aria-selected="true"] .react-aria-SelectionIndicator {
  background-color: #2563EB !important;
}
@media (max-width: 767px) {
  h1 { font-size: 1.75rem !important; line-height: 1.18 !important; }
  .dashboard-status { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .dashboard-status__item { border-bottom: 1px solid #EDF1F5; }
  .dashboard-status__item:nth-child(2n) { border-right: 0; }
  .dashboard-status__item:nth-last-child(-n + 2) { border-bottom: 0; }
  .weather-source-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
"""


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--db", type=Path, default=Path(os.environ.get("NICE_WEATHER_DB", "var/fixture.sqlite3"))
    )
    parser.add_argument("--refresh-seconds", type=int, default=10)
    args, _ = parser.parse_known_args()
    return args


def probability_figure(outcomes: list[dict[str, object]]) -> go.Figure:
    labels = [str(item["label"]) for item in outcomes]
    figure = go.Figure()
    figure.add_bar(
        name="Model probability",
        x=labels,
        y=[item["model_probability"] for item in outcomes],
        marker_color="#356AE6",
    )
    figure.add_scatter(
        name="Best Bid",
        x=labels,
        y=[item["best_bid"] for item in outcomes],
        mode="lines+markers",
        line={"color": "#667085"},
    )
    figure.add_scatter(
        name="Best Ask",
        x=labels,
        y=[item["best_ask"] for item in outcomes],
        mode="lines+markers",
        line={"color": "#E66A4E"},
    )
    approved = [item for item in outcomes if item["risk_approved"]]
    if approved:
        figure.add_scatter(
            name="Risk-approved candidate",
            x=[item["label"] for item in approved],
            y=[item["model_probability"] for item in approved],
            mode="markers+text",
            text=[f"★ pos {item['paper_position']:.2f}" for item in approved],
            textposition="top center",
            marker={"symbol": "star", "size": 14, "color": "#ffbf00"},
        )
    figure.update_layout(
        barmode="group",
        yaxis_title="Probability / price ($)",
        xaxis_title="Final KLGA Tmax bin (°F)",
        template="plotly_white",
        margin={"l": 24, "r": 16, "t": 24, "b": 24},
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )
    return figure


def weather_figure(
    weather: dict[str, list[dict[str, object]]],
    summary: dict[str, object],
    display_zone: tzinfo,
    timezone_name: str,
) -> go.Figure:
    figure = go.Figure()
    observations = weather["observations"]
    forecasts = weather["forecasts"]
    if observations:
        figure.add_scatter(
            name="KLGA METAR observed",
            x=[_display_datetime(item["observed_at"], display_zone) for item in observations],
            y=[item["temperature_f"] for item in observations],
            customdata=[
                [_format_timestamp(item["received_at"], display_zone), item["snapshot_id"]]
                for item in observations
            ],
            hovertemplate="%{x}<br>%{y:.1f}°F<br>received %{customdata[0]}<extra></extra>",
        )
    if forecasts:
        figure.add_scatter(
            name="NWS hourly forecast",
            x=[_display_datetime(item["valid_at"], display_zone) for item in forecasts],
            y=[item["temperature_f"] for item in forecasts],
            line={"dash": "dot"},
            customdata=[
                [
                    _format_timestamp(item["issued_at"], display_zone),
                    _format_timestamp(item["received_at"], display_zone),
                ]
                for item in forecasts
            ],
            hovertemplate="%{x}<br>%{y:.1f}°F<br>issued %{customdata[0]}<extra></extra>",
        )
    probability = summary["probability_summary"]
    figure.add_hline(
        y=probability["baseline_tmax_f"], line_dash="dash", annotation_text="Baseline Tmax"
    )
    figure.add_hrect(
        y0=probability["interval_low_f"],
        y1=probability["interval_high_f"],
        opacity=0.12,
        line_width=0,
        annotation_text="80% baseline interval",
    )
    figure.update_layout(
        yaxis_title="Temperature (°F)",
        xaxis_title=f"Observation / valid time ({timezone_name})",
    )
    return figure


def depth_figure(levels: list[dict[str, object]]) -> go.Figure:
    figure = go.Figure()
    for side, color in (("bid", "#667085"), ("ask", "#E76F51")):
        selected = [item for item in levels if item["side"] == side]
        figure.add_bar(
            name=side.title(),
            y=[str(item["price"]) for item in selected],
            x=[item["size"] for item in selected],
            orientation="h",
            marker_color=color,
        )
    figure.update_layout(
        barmode="group",
        xaxis_title="Displayed token quantity",
        yaxis_title="Price ($)",
        template="plotly_white",
        margin={"l": 24, "r": 16, "t": 24, "b": 24},
        legend={"orientation": "h", "y": 1.08, "x": 0},
    )
    return figure


def _money(value: object, decimals: int = 3) -> str:
    return f"${float(value):.{decimals}f}" if value is not None else "Unavailable"


def _age(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    seconds = max(0, round(value))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3_600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86_400:
        return f"{seconds // 3_600}h {(seconds % 3_600) // 60}m"
    return f"{seconds // 86_400}d {(seconds % 86_400) // 3_600}h"


def _status_grid(items: tuple[tuple[str, object], ...]) -> None:
    cells = "".join(
        '<div class="dashboard-status__item">'
        f'<span class="dashboard-status__label">{escape(str(label))}</span>'
        f'<span class="dashboard-status__value" title="{escape(str(value))}">'
        f"{escape(str(value))}</span></div>"
        for label, value in items
    )
    st.markdown(f'<div class="dashboard-status">{cells}</div>', unsafe_allow_html=True)


def _display_timezone(timezone_name: str | None) -> tuple[tzinfo, str]:
    del timezone_name
    return _NEW_YORK, "ET"


def _browser_timezone_note(timezone_name: str | None, now: datetime | None = None) -> str:
    if not timezone_name:
        return "Browser timezone unavailable"
    try:
        browser_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return "Browser timezone unavailable"
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    browser_offset = instant.astimezone(browser_zone).utcoffset()
    new_york_offset = instant.astimezone(_NEW_YORK).utcoffset()
    if browser_offset is None or new_york_offset is None:
        return "Browser timezone unavailable"
    seconds = int((new_york_offset - browser_offset).total_seconds())
    if seconds == 0:
        relation = "Same time"
    else:
        sign = "+" if seconds > 0 else "-"
        absolute = abs(seconds)
        hours, remainder = divmod(absolute, 3600)
        minutes = remainder // 60
        relation = f"New York {sign}{hours}h" + (f" {minutes}m" if minutes else "")
    return f"Browser: {timezone_name} · {relation}"


def _display_datetime(value: object, zone: tzinfo) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(zone)


def _format_timestamp(value: object, zone: tzinfo) -> str:
    parsed = _display_datetime(value, zone)
    return parsed.strftime("%F %T") + " ET" if parsed is not None else "Unavailable"


def _normalized_url(value: str) -> tuple[str, str, tuple[tuple[str, str], ...]] | None:
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    query = tuple(sorted((key.lower(), item.lower()) for key, item in parse_qsl(parsed.query)))
    return parsed.hostname.lower(), parsed.path.rstrip("/").lower(), query


def _resolution_source_matches(contract_source: str, evidence_source: str) -> bool:
    contract = _normalized_url(contract_source)
    evidence = _normalized_url(evidence_source)
    return contract is not None and evidence is not None and contract == evidence


def _is_timestamp_key(key: str) -> bool:
    return key.endswith("_at") or key in _TIMESTAMP_KEYS


def _localize_record(record: dict[str, Any], zone: tzinfo) -> dict[str, Any]:
    localized: dict[str, Any] = {}
    for key, value in record.items():
        if value is not None and _is_timestamp_key(key):
            localized[key] = _format_timestamp(value, zone)
        elif isinstance(value, dict):
            localized[key] = _localize_record(value, zone)
        elif isinstance(value, list):
            localized[key] = [
                _localize_record(item, zone) if isinstance(item, dict) else item for item in value
            ]
        else:
            localized[key] = value
    return localized


def _localize_records(records: list[dict[str, Any]], zone: tzinfo) -> list[dict[str, Any]]:
    return [_localize_record(record, zone) for record in records]


# Reused across tick sorting, expiry, selection and minute reconstruction; bounded across sessions.
@lru_cache(maxsize=131_072)
def _epoch(value: object) -> float:
    parsed = _display_datetime(value, UTC)
    if parsed is None:
        raise ValueError(f"Invalid timestamp: {value}")
    return parsed.timestamp()


def _points(rows: list[dict[str, Any]], time_key: str, value_key: str) -> list[dict[str, object]]:
    points: list[dict[str, object]] = []
    for row in rows:
        value = _finite_float(row.get(value_key))
        try:
            timestamp = _epoch(row.get(time_key))
        except ValueError:
            logger.warning("repricing_invalid_timestamp series=%s", time_key)
            continue
        if value is None:
            logger.warning("repricing_invalid_value series=%s time=%s", value_key, timestamp)
            continue
        points.append(
            {
                "time": timestamp,
                "value": value,
                "object_time": row[time_key],
                "received_at": row.get("received_at"),
            }
        )
    return points


def _finite_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _price_display_value(raw_value: object) -> float | None:
    value = _finite_float(raw_value)
    if value is None or value < 0 or value > 1:
        return None
    return value * 100


def _price_kind(tick: dict[str, Any]) -> str | None:
    if tick.get("event_kind") == "trade":
        return "trade" if tick.get("source") == "clob_ws" else None
    if tick.get("source") == "clob_ws":
        return "clob"
    if tick.get("source") == "gamma_fallback":
        return "gamma"
    return None


def _price_state_selection(
    latest_clob: dict[str, Any] | None,
    latest_trade: dict[str, Any] | None,
    latest_gamma: dict[str, Any] | None,
    at: datetime,
) -> dict[str, Any] | None:
    cutoff = at.astimezone(UTC).timestamp()
    if latest_clob:
        bid = _finite_float(latest_clob.get("best_bid"))
        ask = _finite_float(latest_clob.get("best_ask"))
        mid = _finite_float(latest_clob.get("mid"))
        display = _price_display_value(mid)
        if (
            latest_clob.get("status") in {"available", "reconnect_snapshot"}
            and bid is not None
            and ask is not None
            and 0 <= bid <= ask <= 1
            and 0 <= cutoff - _epoch(latest_clob["received_at"]) <= 600
            and mid is not None
            and display is not None
        ):
            return {
                "value": mid,
                "display_value": display,
                "source": "CLOB mid",
                "time": latest_clob["exchange_event_at"],
                "received_at": latest_clob.get("received_at"),
                "age_seconds": max(0.0, cutoff - _epoch(latest_clob["exchange_event_at"])),
                "bin_id": latest_clob.get("bin_id"),
                "quality": "verified" if latest_clob.get("event_kind") else "legacy-unverified",
            }
    if latest_trade:
        trade = _finite_float(latest_trade.get("last_trade_price"))
        display = _price_display_value(trade)
        age = cutoff - _epoch(latest_trade["exchange_event_at"])
        if trade is not None and display is not None and 0 <= age <= 300:
            return {
                "value": trade,
                "display_value": display,
                "source": "Last trade",
                "time": latest_trade["exchange_event_at"],
                "received_at": latest_trade.get("received_at"),
                "age_seconds": age,
                "bin_id": latest_trade.get("bin_id"),
            }
    if latest_gamma:
        mid = _finite_float(latest_gamma.get("mid"))
        display = _price_display_value(mid)
        age = cutoff - _epoch(latest_gamma["exchange_event_at"])
        if mid is not None and display is not None and 0 <= age <= 600:
            return {
                "value": mid,
                "display_value": display,
                "source": "Gamma approximate",
                "time": latest_gamma["exchange_event_at"],
                "received_at": latest_gamma.get("received_at"),
                "age_seconds": age,
                "bin_id": latest_gamma.get("bin_id"),
            }
    return None


def _select_price(ticks: list[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    cutoff = at.astimezone(UTC).timestamp()

    def known(item: dict[str, Any]) -> bool:
        try:
            return (
                _epoch(item["exchange_event_at"]) <= cutoff
                and _epoch(item["received_at"]) <= cutoff
            )
        except (KeyError, ValueError):
            logger.warning("repricing_invalid_price_time tick=%s", item.get("tick_id"))
            return False

    eligible = [item for item in ticks if known(item)]

    def receipt_key(item: dict[str, Any]) -> tuple[float, str]:
        return _epoch(item["received_at"]), str(item["tick_id"])

    clob = [item for item in eligible if _price_kind(item) == "clob"]
    trades = [
        item
        for item in eligible
        if _price_kind(item) == "trade" and item.get("last_trade_price") is not None
    ]
    gamma = [
        item
        for item in eligible
        if item.get("source") == "gamma_fallback"
        and item.get("mid") is not None
        and item.get("status") not in {"crossed", "disconnect", "missing"}
    ]
    return _price_state_selection(
        max(clob, key=receipt_key) if clob else None,
        max(trades, key=receipt_key) if trades else None,
        max(gamma, key=receipt_key) if gamma else None,
        at,
    )


def _price_points(
    ticks: list[dict[str, Any]],
    selected_bin_id: str,
    start_at: datetime,
    end_at: datetime | None = None,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Price changes when known, including expiration without new ticks."""
    stop = min(
        (end_at or datetime.max.replace(tzinfo=UTC)).timestamp(),
        (as_of or datetime.now(UTC)).timestamp(),
    )
    ordered = sorted(
        ticks,
        key=lambda row: (
            max(_epoch(row["received_at"]), _epoch(row["exchange_event_at"])),
            row["received_at"],
            row["tick_id"],
        ),
    )
    times = {start_at.timestamp(), stop}
    for row in ordered:
        known = max(_epoch(row["received_at"]), _epoch(row["exchange_event_at"]))
        times.add(known)
        kind = _price_kind(row)
        expiry = (
            _epoch(row["received_at"]) + 600
            if kind == "clob"
            else _epoch(row["exchange_event_at"]) + (300 if kind == "trade" else 600)
        )
        times.add(expiry + 0.001)
    state: dict[str, dict[str, Any]] = {}
    index = 0
    result = []
    for target in sorted(t for t in times if start_at.timestamp() <= t <= stop):
        while index < len(ordered):
            row = ordered[index]
            if max(_epoch(row["received_at"]), _epoch(row["exchange_event_at"])) > target:
                break
            kind = _price_kind(row)
            if kind:
                current = state.get(kind)
                if current is None or (row["received_at"], row["tick_id"]) > (
                    current["received_at"],
                    current["tick_id"],
                ):
                    state[kind] = row
            index += 1
        selected = _price_state_selection(
            state.get("clob"),
            state.get("trade"),
            state.get("gamma"),
            datetime.fromtimestamp(target, UTC),
        )
        result.append(
            {
                "time": target,
                "value": selected["value"] if selected else None,
                "rawValue": selected["value"] if selected else None,
                "rawUnit": "probability",
                "priceSource": selected["source"] if selected else "Unavailable",
                "objectTime": selected["time"] if selected else None,
                "receivedAt": selected.get("received_at") if selected else None,
                "displayValue": selected["display_value"] if selected else None,
                "quality": selected.get("quality") if selected else None,
                "binId": selected_bin_id,
            }
        )
    return result


def _minute_grid(start: datetime, end: datetime, as_of: datetime) -> list[int]:
    first = math.floor(start.astimezone(UTC).timestamp() / 60) * 60
    stop = min(end.astimezone(UTC).timestamp(), as_of.astimezone(UTC).timestamp())
    stop_boundary = math.ceil(stop / 60) * 60
    return list(range(first, stop_boundary, 60)) if first < stop else []


def _temperature_point(
    row: dict[str, Any] | None, target: int, freshness_seconds: int, source: str
) -> dict[str, Any]:
    if row is None:
        return {"time": target, "value": None, "reason": "no-records"}
    observed = _epoch(row["observed_at"])
    age = target - observed
    value = _finite_float(row.get("temperature_f"))
    if value is None or age < 0 or age > freshness_seconds:
        return {
            "time": target,
            "value": None,
            "reason": "expired" if age > freshness_seconds else "invalid-input",
        }
    return {
        "time": target,
        "value": value,
        "rawValue": value,
        "rawUnit": "°F",
        "source": source,
        "objectTime": row["observed_at"],
        "receivedAt": row.get("received_at"),
        "ageSeconds": age,
    }


def _forecast_point(
    snapshots: list[dict[str, Any]], target: int, max_issue_age_seconds: int
) -> dict[str, Any]:
    reason = "no-records"
    for snapshot in reversed(snapshots):
        received = snapshot["received_epoch"]
        issued = snapshot["issued_epoch"]
        if received > target or issued > target:
            continue
        reason = "expired" if target - issued > max_issue_age_seconds else "missing-forecast"
        if reason == "expired":
            break
        times: list[int] = snapshot["times"]
        index = bisect_left(times, target)
        if index < len(times) and times[index] == target:
            left = right = snapshot["points"][index]
            value = _finite_float(left.get("temperature_f"))
        elif index == 0 or index >= len(times):
            break
        else:
            left = snapshot["points"][index - 1]
            right = snapshot["points"][index]
            left_value = _finite_float(left.get("temperature_f"))
            right_value = _finite_float(right.get("temperature_f"))
            if left_value is None or right_value is None or times[index] == times[index - 1]:
                break
            ratio = (target - times[index - 1]) / (times[index] - times[index - 1])
            value = left_value + (right_value - left_value) * ratio
        if value is None or not math.isfinite(value):
            break
        return {
            "time": target,
            "value": value,
            "rawValue": value,
            "rawUnit": "°F",
            "source": "NWS Hourly Forecast",
            "objectTime": datetime.fromtimestamp(target, UTC).isoformat(),
            "receivedAt": snapshot["received_at"],
            "issuedAt": snapshot["issued_at"],
            "validFrom": left["valid_at"],
            "validTo": right["valid_at"],
            "captureId": snapshot["capture_id"],
            "ageSeconds": target - received,
        }
    return {"time": target, "value": None, "reason": reason}


def _forecast_snapshots(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        capture_id = str(
            row.get("capture_id")
            or row.get("legacy_snapshot_id")
            or f"{row.get('issued_at')}|{row.get('received_at')}"
        )
        grouped.setdefault(capture_id, []).append(row)
    result = []
    for capture_id, points in grouped.items():
        valid = [
            item
            for item in points
            if item.get("valid_at") is not None
            and item.get("received_at") is not None
            and item.get("issued_at") is not None
        ]
        if not valid:
            continue
        valid.sort(key=lambda item: (_epoch(item["valid_at"]), str(item["forecast_point_id"])))
        by_time = {_epoch(item["valid_at"]): item for item in valid}
        ordered = [by_time[key] for key in sorted(by_time)]
        received_at = max(valid, key=lambda item: _epoch(item["received_at"]))["received_at"]
        result.append(
            {
                "capture_id": capture_id,
                "issued_at": ordered[0]["issued_at"],
                "received_at": received_at,
                "issued_epoch": _epoch(ordered[0]["issued_at"]),
                "received_epoch": _epoch(received_at),
                "times": [int(_epoch(item["valid_at"])) for item in ordered],
                "points": ordered,
            }
        )
    return sorted(result, key=lambda item: (item["received_epoch"], item["capture_id"]))


def _repricing_difference_inputs(
    history: dict[str, list[dict[str, Any]]],
    ticks: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    as_of: datetime,
    selected_bin_id: str,
    observation_age_seconds: int,
    forecast_issue_seconds: int,
) -> list[dict[str, Any]]:
    wrong_bins = {
        str(item.get("bin_id"))
        for item in ticks
        if item.get("bin_id") is not None and str(item.get("bin_id")) != selected_bin_id
    }
    if wrong_bins:
        raise ValueError(f"Repricing price input contains unexpected bin IDs: {sorted(wrong_bins)}")

    grid = _minute_grid(start, end, as_of)
    observations = history.get("observations", [])
    sources = {
        "metar": sorted(
            (item for item in observations if item.get("source") == "aviationweather"),
            key=lambda item: (
                max(_epoch(item["received_at"]), _epoch(item["observed_at"])),
                str(item["observation_id"]),
            ),
        ),
        "weather-gov": sorted(
            history.get("settlement_rows", []),
            key=lambda item: (
                max(_epoch(item["received_at"]), _epoch(item["observed_at"])),
                str(item["row_id"]),
            ),
        ),
    }
    source_names = {
        "metar": "AviationWeather METAR",
        "weather-gov": "Weather.gov Hourly Temp",
    }
    source_state: dict[str, dict[str, Any] | None] = {key: None for key in sources}
    source_index = {key: 0 for key in sources}
    snapshots = _forecast_snapshots(history.get("forecasts", []))
    ordered_ticks = sorted(
        ticks,
        key=lambda item: (
            max(_epoch(item["received_at"]), _epoch(item["exchange_event_at"])),
            _epoch(item["received_at"]),
            str(item["tick_id"]),
        ),
    )
    tick_index = 0
    latest_clob: dict[str, Any] | None = None
    latest_trade: dict[str, Any] | None = None
    latest_gamma: dict[str, Any] | None = None
    points: dict[str, list[dict[str, Any]]] = {
        "forecast": [],
        "weather-gov": [],
        "metar": [],
        "price": [],
    }

    for target in grid:
        # Empty history is the price-only incremental path.
        for source_id, rows in sources.items() if history else []:
            while source_index[source_id] < len(rows):
                row = rows[source_index[source_id]]
                if max(_epoch(row["received_at"]), _epoch(row["observed_at"])) > target:
                    break
                current = source_state[source_id]
                candidate_key = (
                    _epoch(row["observed_at"]),
                    int(row.get("revision") or 0),
                    _epoch(row["received_at"]),
                    str(row.get("observation_id") or row.get("row_id") or ""),
                )
                current_key = (
                    (
                        _epoch(current["observed_at"]),
                        int(current.get("revision") or 0),
                        _epoch(current["received_at"]),
                        str(current.get("observation_id") or current.get("row_id") or ""),
                    )
                    if current is not None
                    else None
                )
                if current_key is None or candidate_key > current_key:
                    source_state[source_id] = row
                source_index[source_id] += 1
            points[source_id].append(
                _temperature_point(
                    source_state[source_id],
                    target,
                    observation_age_seconds,
                    source_names[source_id],
                )
            )

        if history:
            points["forecast"].append(_forecast_point(snapshots, target, forecast_issue_seconds))

        while tick_index < len(ordered_ticks):
            tick = ordered_ticks[tick_index]
            if max(_epoch(tick["received_at"]), _epoch(tick["exchange_event_at"])) > target:
                break
            receipt_key = (_epoch(tick["received_at"]), str(tick["tick_id"]))
            if _price_kind(tick) == "clob" and (
                latest_clob is None
                or receipt_key > (_epoch(latest_clob["received_at"]), str(latest_clob["tick_id"]))
            ):
                latest_clob = tick
            if (
                _price_kind(tick) == "trade"
                and tick.get("last_trade_price") is not None
                and (
                    latest_trade is None
                    or receipt_key
                    > (_epoch(latest_trade["received_at"]), str(latest_trade["tick_id"]))
                )
            ):
                latest_trade = tick
            if tick.get("source") == "gamma_fallback" and (
                latest_gamma is None
                or receipt_key > (_epoch(latest_gamma["received_at"]), str(latest_gamma["tick_id"]))
            ):
                latest_gamma = tick
            tick_index += 1
        selected = _price_state_selection(
            latest_clob,
            latest_trade,
            latest_gamma,
            datetime.fromtimestamp(target, UTC),
        )
        display = selected.get("display_value") if selected else None
        if selected is None or display is None or selected.get("bin_id") != selected_bin_id:
            points["price"].append({"time": target, "value": None, "binId": selected_bin_id})
        else:
            points["price"].append(
                {
                    "time": target,
                    "value": display,
                    "rawValue": selected["value"],
                    "rawUnit": "probability",
                    "displayValue": display,
                    "source": selected["source"],
                    "priceSource": selected["source"],
                    "objectTime": selected["time"],
                    "receivedAt": selected.get("received_at"),
                    "ageSeconds": selected["age_seconds"],
                    "binId": selected_bin_id,
                }
            )

    return [
        {"id": "forecast", "name": "Forecast", "points": points["forecast"]},
        {
            "id": "weather-gov",
            "name": "Weather.gov Hourly Temp",
            "points": points["weather-gov"],
        },
        {"id": "metar", "name": "METAR", "points": points["metar"]},
        {"id": "price", "name": "Price × 100", "binId": selected_bin_id, "points": points["price"]},
    ]


def _default_timeline_bin(
    bins: list[dict[str, Any]],
    running_tmax_f: float | None,
    probabilities: dict[str, float] | None = None,
) -> str | None:
    if not bins:
        return None
    target_index = max(
        range(len(bins)),
        key=lambda index: (probabilities or {}).get(str(bins[index]["bin_id"]), -1.0),
    )
    if running_tmax_f is not None:
        contract_temperature = math.floor(running_tmax_f + 0.5)
        for index, item in enumerate(bins):
            lower = item.get("lower_bound")
            upper = item.get("upper_bound")
            if (lower is None or float(lower) <= contract_temperature) and (
                upper is None or contract_temperature <= float(upper)
            ):
                target_index = index
                break
    return str(bins[target_index]["bin_id"])


def _timeline_series(
    timeline: dict[str, list[dict[str, Any]]],
    ticks: list[dict[str, Any]],
    visible_sources: list[str],
    freshness_seconds: int,
    now: datetime,
    selected_bin_id: str,
    start: datetime,
) -> list[dict[str, Any]]:
    metar = [item for item in timeline["observations"] if item["source"] == "aviationweather"]
    nws = [item for item in timeline["observations"] if item["source"] == "nws"]
    rows = {
        "forecast": (timeline["forecasts"], "valid_at", "temperature_f", "dashed"),
        "weather-gov": (timeline["running_tmax"], "observed_at", "temperature_f", "solid"),
        "metar": (metar, "observed_at", "temperature_f", "solid"),
        "nws-observations": (nws, "observed_at", "temperature_f", "dotted"),
    }
    result: list[dict[str, Any]] = []
    for source_id in visible_sources:
        spec = _SOURCE_SPECS[source_id]
        source_rows, time_key, value_key, style = rows[source_id]
        points = _points(source_rows, time_key, value_key)
        for point in points:
            point["rawValue"] = point["value"]
            point["rawUnit"] = "°F"
        result.append(
            {
                "id": source_id,
                "name": spec["name"],
                "description": spec["description"],
                "group": "Weather",
                "pane": "weather",
                "format": "temperature",
                "fill": spec["kind"],
                "maxAgeSeconds": freshness_seconds if spec["kind"] == "step-fresh" else None,
                "color": spec["color"],
                "lineStyle": style,
                "points": points,
            }
        )
    current_price = _select_price(ticks, now)
    if current_price is not None:
        current_price["bin_id"] = selected_bin_id
    result.append(
        {
            "id": "price",
            "name": "Price",
            "description": "Selected-bin price using CLOB mid, recent trade, then Gamma fallback.",
            "group": "Market",
            "pane": "market",
            "format": "probability",
            "fill": "price",
            "binId": selected_bin_id,
            "color": "#2563EB",
            "lineStyle": "solid",
            "points": _price_points(ticks, selected_bin_id, start, as_of=now),
            "currentPrice": current_price,
        }
    )
    return result


def _merge_input_points(previous: list[dict], incoming: list[dict]) -> list[dict]:
    points = {point["time"]: point for point in previous}
    points.update({point["time"]: point for point in incoming})
    return [points[stamp] for stamp in sorted(points)]


def _future_inputs(
    history: dict,
    price: dict | None,
    start: datetime,
    end: datetime,
    now: datetime,
    forecast_age: int,
    bin_id: str,
) -> list[dict]:
    snapshots = [
        snapshot
        for snapshot in _forecast_snapshots(history["forecasts"])
        if snapshot["received_epoch"] <= now.timestamp()
        and 0 <= now.timestamp() - snapshot["issued_epoch"] <= forecast_age
    ]
    snapshots = snapshots[-1:]
    forecast, prices = [], []
    for target in _minute_grid(start, end, end):
        forecast.append(_forecast_point(snapshots, target, 10**9))
        prices.append(
            {
                "time": target,
                "value": price["display_value"] if price else None,
                "rawValue": price["value"] if price else None,
                "rawUnit": "probability",
                "binId": bin_id,
                "priceSource": price["source"] if price else None,
                "objectTime": price["time"] if price else None,
                "receivedAt": price.get("received_at") if price else None,
            }
        )
    return [
        {"id": "forecast", "name": "Forecast", "points": forecast},
        {"id": "weather-gov", "name": "Weather.gov Hourly Temp", "points": []},
        {"id": "metar", "name": "METAR", "points": []},
        {"id": "price", "name": "Price × 100", "binId": bin_id, "points": prices},
    ]


def _timeline_data(
    db: Path,
    event_id: str,
    object_day: date,
    object_timezone: str,
    horizon: int,
    selected_bin_id: str,
    visible_sources: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], datetime, datetime]:
    started = perf_counter()
    query = DashboardQuery(db)
    now = datetime.now(UTC)
    zone = ZoneInfo(object_timezone)
    start = datetime.combine(object_day, time.min, zone).astimezone(UTC)
    end = (datetime.combine(object_day, time.min, zone) + timedelta(days=1)).astimezone(UTC)
    future = start > now
    cutoff = min(now, end - timedelta(microseconds=1))
    config = load_city_config()
    age = config.freshness.observation_age_seconds
    issue_age = config.freshness.forecast_issue_seconds
    key = (str(db.resolve()), event_id, object_day.isoformat())
    cache = st.session_state.setdefault("_repricing_data_cache", {})
    # ponytail: keep only the selected day; add a bounded day LRU if day switching warrants it.
    if cache.get("key") != key:
        cache.clear()
        cache.update(key=key, bins={})
    version = query.repricing_weather_version()
    minute = int(cutoff.timestamp() // 60)
    weather_changed = cache.get("version") != version
    if weather_changed:
        cache["history"] = query.get_repricing_weather_history(
            object_day, 1, now, object_timezone, age
        )
        cache["timeline"] = query.get_weather_timeline(object_day, 1, now, object_timezone)
        cache["events"] = [
            {
                "id": f"forecast:{row['capture_id']}",
                "type": "forecast_revised",
                "time": _epoch(row["received_at"]),
                "title": f"Forecast revised to {row['forecast_tmax_f']:.0f}°F",
            }
            for row in query.get_forecast_revision_events(object_day, now, object_timezone)
        ]
        cache["version"] = version
    if weather_changed or cache.get("minute") != minute:
        weather_start = (
            start
            if weather_changed or "weather" not in cache
            else datetime.fromtimestamp(cache["minute"] * 60, UTC)
        )
        updated = _repricing_difference_inputs(
            cache["history"], [], weather_start, end, cutoff, selected_bin_id, age, issue_age
        )
        nws_history = {
            "observations": [
                {**row, "source": "aviationweather"}
                for row in cache["history"]["observations"]
                if row.get("source") == "nws"
            ]
        }
        nws_points = _repricing_difference_inputs(
            nws_history, [], weather_start, end, cutoff, selected_bin_id, age, issue_age
        )[2]["points"]
        cache["nws"] = _merge_input_points(
            [] if weather_changed else cache.get("nws", []),
            [{**point, "source": "NWS Station Observations"} for point in nws_points],
        )
        previous = {item["id"]: item for item in cache.get("weather", [])}
        cache["weather"] = [
            {
                **item,
                "points": _merge_input_points(
                    previous.get(item["id"], {}).get("points", []), item["points"]
                ),
            }
            if not weather_changed
            else item
            for item in updated
            if item["id"] != "price"
        ]
        cache["minute"] = minute
    market = cache["bins"].setdefault(selected_bin_id, {"ticks": [], "cursor": None})
    market_start = (now if future else start) - timedelta(seconds=600)
    incoming = query.get_repricing_ticks(
        event_id, selected_bin_id, market_start, end, now if future else cutoff, market["cursor"]
    )
    market["ticks"].extend(incoming["ticks"])
    market["cursor"] = incoming["cursor"]
    ticks = market["ticks"]
    current_price = _select_price(ticks, now if future else cutoff)
    if future:
        inputs = _future_inputs(
            cache["history"], current_price, start, end, now, issue_age, selected_bin_id
        )
    else:
        # Recompute only minutes affected by newly received events, plus the advancing boundary.
        dirty = market.get("minute", int(start.timestamp() // 60)) * 60
        if incoming["ticks"]:
            dirty = min(
                dirty,
                min(
                    max(_epoch(row["received_at"]), _epoch(row["exchange_event_at"]))
                    for row in incoming["ticks"]
                ),
            )
        dirty = max(start.timestamp(), math.floor(dirty / 60) * 60)
        tail = [row for row in ticks if _epoch(row["received_at"]) >= dirty - 600]
        if incoming["ticks"] or market.get("minute") != minute or "price_input" not in market:
            price_input = _repricing_difference_inputs(
                {},
                tail,
                datetime.fromtimestamp(dirty, UTC),
                end,
                cutoff,
                selected_bin_id,
                age,
                issue_age,
            )[-1]
            price_input["points"] = _merge_input_points(
                market.get("price_input", {}).get("points", []), price_input["points"]
            )
            market["price_input"] = price_input
            market["minute"] = minute
        inputs = [*cache["weather"], market["price_input"]]
        old_main = market.get("main_points", [])
        main_tail = _price_points(
            tail, selected_bin_id, datetime.fromtimestamp(dirty, UTC), end, cutoff
        )
        market["main_points"] = [point for point in old_main if point["time"] < dirty] + main_tail

    series = _timeline_series(
        cache["timeline"], [], visible_sources, age, cutoff, selected_bin_id, start
    )
    by_id = {item["id"]: item for item in inputs}
    for item in series:
        if item["id"] == "nws-observations":
            item["points"] = cache["nws"]
        if item["id"] in {"forecast", "weather-gov", "metar"}:
            item["points"] = list(by_id[item["id"]]["points"])
            if item["id"] == "weather-gov":
                item.update(
                    name="Weather.gov Hourly Temp",
                    fill="step-fresh",
                    maxAgeSeconds=age,
                    description="Official hourly temperature, as known at each minute.",
                )
            if item["id"] == "forecast" and not future and now < end:
                forward = _future_inputs(
                    cache["history"], None, now, end, now, issue_age, selected_bin_id
                )[0]["points"]
                item["points"] = [
                    *item["points"],
                    *[point for point in forward if point["time"] > now.timestamp()],
                ]
        if item["id"] == "price":
            item["currentPrice"] = current_price
            item["points"] = (
                [
                    {**point, "value": point["value"] / 100 if point["value"] is not None else None}
                    for point in by_id["price"]["points"]
                ]
                if future
                else market["main_points"]
            )
            item["status"] = (
                "available"
                if current_price
                else "no-records"
                if not ticks
                else "disconnected"
                if ticks[-1].get("status") == "disconnect"
                else "expired"
            )
        else:
            item["status"] = (
                "available"
                if any(p["value"] is not None for p in item["points"])
                else ("not-yet-observed" if future else "no-records")
            )
        item["validTo"] = end.timestamp()
    revisions = cache["events"]
    st.session_state["_repricing_meta"] = {
        "comparisonMode": "future-snapshot" if future else "as-of",
        "asOf": now.timestamp(),
        "latestActualTime": max(
            [start.timestamp()]
            + [
                _epoch(p["objectTime"])
                for item in series
                if item["id"] != "forecast"
                for p in item["points"]
                if p.get("value") is not None
                and p["time"] <= min(now.timestamp(), end.timestamp())
                and p.get("objectTime") is not None
                and _epoch(p["objectTime"]) >= start.timestamp()
            ]
            + [
                max(_epoch(row["exchange_event_at"]), _epoch(row["received_at"]))
                for row in ticks
                if _epoch(row["received_at"]) <= cutoff.timestamp()
                and _epoch(row["exchange_event_at"]) <= cutoff.timestamp()
            ]
        ),
        "queryMs": round((perf_counter() - started) * 1000, 2),
        "legacyWarning": any(row.get("event_kind") is None for row in ticks),
    }
    return series, inputs, revisions, start, end


def _series_delta(
    series: list[dict[str, Any]], previous: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    delta: list[dict[str, Any]] = []
    current: dict[str, dict[str, Any]] = {}
    for item in series:
        series_id = str(item["id"])
        prior = previous.get(series_id, {})
        # Scalar point fields can be compared directly without serializing the entire day.
        hashes = {str(point["time"]): dict(point) for point in item["points"]}
        changed = [
            point
            for point in item["points"]
            if prior.get(str(point["time"])) != hashes[str(point["time"])]
        ]
        delta.append(
            {
                **item,
                "points": changed,
                "removedTimes": [float(t) for t in prior if t not in hashes],
            }
        )
        current[series_id] = hashes
    return delta, current


@st.fragment(run_every="2s")
def _render_repricing_feed(
    db: Path,
    event_id: str,
    object_day_text: str,
    object_timezone: str,
    horizon: int,
    selected_bin_id: str,
    visible_sources: tuple[str, ...],
    channel_id: str,
    signature: str,
) -> None:
    if st.session_state.get("_repricing_feed_signature") != signature:
        return
    if perf_counter() - st.session_state.get("_repricing_full_at", 0) < 1:
        return
    try:
        series, difference_inputs, events, start, end = _timeline_data(
            db,
            event_id,
            date.fromisoformat(object_day_text),
            object_timezone,
            horizon,
            selected_bin_id,
            list(visible_sources),
        )
        state_signature = st.session_state.get("_repricing_feed_signature")
        previous = (
            st.session_state.get("_repricing_feed_hashes", {})
            if state_signature == signature
            else {}
        )
        series_delta, series_hashes = _series_delta(series, previous.get("series", {}))
        difference_delta, difference_hashes = _series_delta(
            difference_inputs, previous.get("difference", {})
        )
        st.session_state["_repricing_feed_signature"] = signature
        st.session_state["_repricing_feed_hashes"] = {
            "series": series_hashes,
            "difference": difference_hashes,
        }
        revision = content_hash(
            {
                "series": [
                    (item["id"], item["points"][-1] if item["points"] else None)
                    for item in series_delta
                ],
                "difference": [
                    (item["id"], item["points"][-1] if item["points"] else None)
                    for item in difference_delta
                ],
            }
        )
        base_sequence = st.session_state.get("_repricing_sequence", 0)
        st.session_state["_repricing_sequence"] = base_sequence + 1
        recovering = st.session_state.pop("_repricing_feed_failed", False)
        trading_chart_feed(
            {
                "mode": "feed",
                "delivery": "full" if recovering else "delta",
                "sequence": base_sequence + 1,
                "baseSequence": base_sequence,
                **st.session_state.get("_repricing_meta", {}),
                "channelId": channel_id,
                "revision": revision,
                "signature": signature,
                "selectedBinId": selected_bin_id,
                "windowStart": start.timestamp(),
                "windowEnd": end.timestamp(),
                "series": series if recovering else series_delta,
                "differenceInputs": difference_inputs if recovering else difference_delta,
                "differenceSpecs": list(_DIFFERENCE_SPECS),
                "events": events,
            },
            key=f"klga-repricing-feed-{channel_id}",
        )
    except Exception:
        st.session_state["_repricing_feed_failed"] = True
        trading_chart_feed(
            {
                "mode": "feed",
                "delivery": "status",
                "channelId": channel_id,
                "signature": signature,
                "selectedBinId": selected_bin_id,
                "error": "Database query failed; showing the last successful update.",
            },
            key=f"klga-repricing-feed-{channel_id}",
        )
        logger.exception(
            "repricing_feed_failed event=%s day=%s range=%s bin=%s signature=%s",
            event_id,
            object_day_text,
            horizon,
            selected_bin_id,
            signature,
        )


@st.fragment
def _render_trading_timeline(db: Path) -> None:
    query = DashboardQuery(db)
    days = query.list_market_days()
    if not days:
        st.info("No repricing market day is available.")
        return
    st.subheader("KLGA Tmax and market repricing")
    day_labels = {f"{item['local_day']} · {item['event_title']}": item for item in days}
    controls = st.columns((2.2, 3.2, 3.2))
    labels = list(day_labels)
    today = datetime.now(_NEW_YORK).date().isoformat()
    default_day = next(
        (i for i, label in enumerate(labels) if str(day_labels[label]["local_day"]) == today), 0
    )
    selected_label = controls[0].selectbox(
        "Market day", labels, index=default_day, key="market_day"
    )
    selected_day = day_labels[selected_label]
    horizon = 1
    event_id = str(selected_day["event_id"])
    object_day = date.fromisoformat(str(selected_day["local_day"]))
    object_timezone = str(selected_day["timezone"])
    bins = query.get_event_bins(event_id)
    bin_ids = [str(item["bin_id"]) for item in bins]
    if not bin_ids:
        st.info("No verified bins are available for this market.")
        return
    if st.session_state.get("selected_bin_id") not in bin_ids:
        running = query.get_weather_timeline(object_day, 1, datetime.now(UTC), object_timezone)[
            "running_tmax"
        ]
        latest_tmax = float(running[-1]["temperature_f"]) if running else None
        st.session_state["selected_bin_id"] = _default_timeline_bin(
            bins, latest_tmax, query.get_latest_event_probabilities(event_id)
        )
    selected_bin_id = str(
        controls[1].radio(
            "Temperature interval",
            bin_ids,
            format_func=lambda value: next(
                str(item["label"]) for item in bins if item["bin_id"] == value
            ),
            horizontal=True,
            key="selected_bin_id",
        )
    )
    future = object_day > datetime.now(ZoneInfo(object_timezone)).date()
    if future:
        controls[2].caption("Future market: NWS Forecast and current Price snapshot")
        selected_sources = ["forecast"]
    else:
        selected_sources = controls[2].multiselect(
            "Weather data sources",
            list(_SOURCE_SPECS),
            default=list(_DEFAULT_SOURCES),
            format_func=lambda value: str(_SOURCE_SPECS[value]["name"]),
            key="visible_source_ids",
        )
    valid_difference_ids = {str(item["id"]) for item in _DIFFERENCE_SPECS}
    selected_differences = [
        str(item)
        for item in st.session_state.setdefault(
            "selected_difference_ids", list(_DEFAULT_DIFFERENCES)
        )
        if str(item) in valid_difference_ids
    ]
    st.session_state["selected_difference_ids"] = selected_differences
    channel_id = st.session_state.setdefault("_repricing_channel_id", uuid.uuid4().hex)
    view_key = (str(db), event_id, object_day, selected_bin_id, tuple(selected_sources))
    view_error = None
    try:
        view = _timeline_data(
            db, event_id, object_day, object_timezone, horizon, selected_bin_id, selected_sources
        )
        st.session_state["_repricing_last_view"] = (view_key, view)
    except Exception:
        logger.exception(
            "repricing_initial_query_failed event=%s bin=%s", event_id, selected_bin_id
        )
        previous = st.session_state.get("_repricing_last_view")
        view_error = "Database query failed; showing the last successful update."
        if previous is None or previous[0] != view_key:
            st.error("Database query failed for this selection. Retry when the database recovers.")
            return
        view = previous[1]
        st.session_state["_repricing_feed_failed"] = True
    series, difference_inputs, events, start, end = view
    signature = content_hash(
        {
            "event": event_id,
            "range": horizon,
            "bin": selected_bin_id,
            "sources": selected_sources,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
    )
    base_sequence = st.session_state.get("_repricing_sequence", 0)
    st.session_state["_repricing_sequence"] = base_sequence + 1
    payload = {
        "mode": "full",
        "error": view_error,
        "sequence": base_sequence + 1,
        "baseSequence": base_sequence,
        **st.session_state.get("_repricing_meta", {}),
        "channelId": channel_id,
        "revision": f"{signature}:{base_sequence + 1}",
        "signature": signature,
        "timezone": "America/New_York",
        "selectedBinId": selected_bin_id,
        "selectedDifferenceIds": ["price-minus-forecast"] if future else selected_differences,
        "windowStart": start.timestamp(),
        "windowEnd": end.timestamp(),
        "series": series,
        "differenceInputs": difference_inputs,
        "differenceSpecs": list(_DIFFERENCE_SPECS),
        "events": events,
    }
    _, series_hashes = _series_delta(series, {})
    _, difference_hashes = _series_delta(difference_inputs, {})
    st.session_state["_repricing_feed_signature"] = signature
    st.session_state["_repricing_feed_hashes"] = {
        "series": series_hashes,
        "difference": difference_hashes,
    }
    st.session_state["_repricing_full_at"] = perf_counter()
    component_state = trading_chart(payload, key="klga-tmax-trading-chart-v4")
    if isinstance(component_state, dict):
        candidates = component_state.get("selectedDifferenceIds")
        if isinstance(candidates, list) and not future:
            st.session_state["selected_difference_ids"] = [
                str(item) for item in candidates if str(item) in valid_difference_ids
            ]
    _render_repricing_feed(
        db,
        event_id,
        object_day.isoformat(),
        object_timezone,
        horizon,
        selected_bin_id,
        tuple(selected_sources),
        channel_id,
        signature,
    )
    st.caption(
        "Difference uses six fixed real-time subtractions. Price comparisons are display spreads "
        "without a shared physical unit; they are visual research aids and are excluded from "
        "trading decisions and historical labels."
    )


def _render(db: Path) -> None:
    query = DashboardQuery(db)
    try:
        summary = query.get_latest_decision_summary()
    except Exception as exc:
        logger.exception("dashboard_database_open_failed")
        st.error(f"Database unavailable: {exc}")
        st.caption(f"Read-only database: {db.resolve()}")
        return
    if summary is None:
        st.info("No completed decision is available. Run the fixture or live-shadow command first.")
        st.caption(f"Read-only database: {db.resolve()}")
        return
    decision_id = str(summary["decision_id"])
    outcomes = query.get_outcome_snapshot(decision_id)
    contract = query.get_contract_view(decision_id)
    health = query.get_health_view(decision_id)
    paper = query.get_paper_view(decision_id)
    model_context = query.get_model_context(decision_id)
    context = getattr(st, "context", None)
    browser_timezone = getattr(context, "timezone", None)
    display_zone, timezone_name = _display_timezone(browser_timezone)
    now = datetime.now(UTC)

    status_values = (
        ("Market day", summary["local_day"]),
        ("Market", "CLOSED" if summary["event_closed"] else "OPEN"),
        ("Mode", summary["mode"]),
        ("Refreshed", now.astimezone(display_zone).strftime("%H:%M:%S") + " ET"),
        ("Decision", summary["overall_action"]),
        ("DataHealth", summary["health_level"]),
        ("City / station", f"{summary['city_code']} / {summary['station_id']}"),
        ("Decision time", _format_timestamp(summary["decision_time"], display_zone)),
    )
    _status_grid(status_values)
    if summary["reason_codes"]:
        st.warning("Reason codes: " + ", ".join(summary["reason_codes"]))

    overview, repricing_tab, execution_tab, paper_tab, system_tab = st.tabs(
        ["Overview", "Repricing", "Execution", "Paper", "System & Audit"]
    )
    with overview:
        st.button("Refresh", icon=":material/refresh:", key="refresh-overview")
        st.caption(_browser_timezone_note(browser_timezone, now))
        with st.expander("Contract and settlement rules", expanded=False):
            st.markdown(f"[{summary['event_title']}]({summary['market_url']})")
            st.dataframe(
                pd.DataFrame([_localize_record(contract["contract"], display_zone)]),
                width="stretch",
            )
            st.dataframe(
                pd.DataFrame(_localize_records(contract["bins"], display_zone)), width="stretch"
            )
        weather = query.get_weather_timeline(
            date.fromisoformat(str(summary["local_day"])), 1, now, str(summary["timezone"])
        )
        metar_rows = [
            item for item in weather["observations"] if item["source"] == "aviationweather"
        ]
        nws_rows = [item for item in weather["observations"] if item["source"] == "nws"]
        source_rows = {
            "forecast": weather["forecasts"],
            "weather-gov": weather["running_tmax"],
            "metar": metar_rows,
            "nws-observations": nws_rows,
        }
        frequency = {
            "forecast": "15 minutes",
            "weather-gov": "hourly; 2 minutes near close",
            "metar": "30 seconds active; 2 minutes otherwise",
            "nws-observations": "5 minutes",
        }
        cards = []
        for source_id, spec in _SOURCE_SPECS.items():
            rows = source_rows[source_id]
            latest = rows[-1] if rows else None
            value = (
                float(latest.get("temperature_f"))
                if latest and latest.get("temperature_f") is not None
                else None
            )
            object_time = latest.get("valid_at") or latest.get("observed_at") if latest else None
            received_at = latest.get("received_at") if latest else None
            received = _display_datetime(received_at, UTC)
            freshness = _age((now - received).total_seconds()) if received else "Unavailable"
            tooltip = (
                f"Data type: {spec['data_type']}. Object time: "
                f"{_format_timestamp(object_time, display_zone)}. "
                f"Update frequency: {frequency[source_id]}. Purpose: {spec['purpose']}. "
                "Latest value: "
                f"{f'{value:.1f}°F' if value is not None else 'Unavailable'}. "
                f"Freshness: {freshness}."
            )
            display_value = f"{value:.1f} °F" if value is not None else "Unavailable"
            cards.append(
                '<div class="weather-source">'
                f'<div class="weather-source__label">{escape(str(spec["name"]))}'
                '<i class="weather-source__info" tabindex="0" '
                f'title="{escape(tooltip)}">i</i></div>'
                f'<div class="weather-source__value">{display_value}</div>'
                "</div>"
            )
        st.markdown(
            '<div class="weather-source-grid">' + "".join(cards) + "</div>",
            unsafe_allow_html=True,
        )
        settlement_source = str(contract["contract"].get("settlement_source") or "")
        configured_source = load_city_config().collector.settlement_url
        if settlement_source:
            host = urlparse(settlement_source).hostname or settlement_source
            st.markdown(
                '<div class="resolution-source">Contract resolution source: '
                f'<a href="{escape(settlement_source)}" target="_blank">{escape(host)}</a></div>',
                unsafe_allow_html=True,
            )
        if _resolution_source_matches(settlement_source, configured_source):
            st.caption(
                "The contract resolution source matches the system settlement evidence source."
            )
        else:
            st.warning(
                "Contract resolution source does not match the system settlement evidence source: "
                f"{settlement_source or 'Unavailable'} · {configured_source}"
            )
        st.subheader("Model probability and executable market prices")
        probability_sum = float(summary["probability_summary"]["probability_sum"])
        if abs(probability_sum - 1.0) > 1e-6:
            st.error(f"Probability sum invalid: {probability_sum:.9f}; candidates are blocked.")
        st.plotly_chart(probability_figure(outcomes), width="stretch")
        overview_rows = [
            {
                "Bin": item["label"],
                "Model probability": item["model_probability"],
                "Bid": item["best_bid"],
                "Ask": item["best_ask"],
                "Net edge": item["net_edge"],
                "Status": item["action"],
            }
            for item in outcomes
        ]
        st.dataframe(pd.DataFrame(overview_rows), width="stretch", hide_index=True)
        with st.expander("Outcome audit fields", expanded=False):
            st.dataframe(
                pd.DataFrame(_localize_records(outcomes, display_zone)),
                width="stretch",
                hide_index=True,
            )
        with st.expander("Model input and capture audit", expanded=False):
            st.json(_localize_record(model_context, display_zone))

    with repricing_tab:
        _render_trading_timeline(db)

    with execution_tab:
        st.button("Refresh", icon=":material/refresh:", key="refresh-execution")
        st.subheader("Executable quote")
        bin_labels = {str(item["bin_id"]): str(item["label"]) for item in outcomes}
        if not bin_labels:
            st.warning("No parsed temperature bins are available for this blocked decision.")
        else:
            selected_bin = str(st.session_state.get("selected_bin_id") or "")
            if selected_bin not in bin_labels:
                selected_bin = next(iter(bin_labels))
            st.caption(f"Repricing interval: {bin_labels[selected_bin]}")
            selected = next(item for item in outcomes if item["bin_id"] == selected_bin)
            quote = query.get_execution_quote(decision_id, selected_bin)
            quote_received = quote.get("received_at") if quote else None
            quote_time = _display_datetime(quote_received, UTC)
            quote_age = (now - quote_time).total_seconds() if quote_time else None
            requested_time = _display_datetime(quote.get("requested_at"), UTC) if quote else None
            quote_latency_ms = (
                (quote_time - requested_time).total_seconds() * 1_000
                if quote_time is not None and requested_time is not None
                else None
            )
            metrics = st.columns(6)
            metrics[0].metric(
                "Best Bid", _money(quote.get("best_bid") if quote else selected["best_bid"])
            )
            metrics[1].metric(
                "Best Ask", _money(quote.get("best_ask") if quote else selected["best_ask"])
            )
            metrics[2].metric("Spread", _money(quote.get("spread") if quote else None))
            metrics[3].metric("Ask VWAP", _money(quote.get("ask_vwap") if quote else None))
            ask_depth = quote.get("ask_depth") if quote else None
            metrics[4].metric(
                "Executable qty",
                f"{float(ask_depth):.2f}" if ask_depth is not None else "Unavailable",
            )
            metrics[5].metric("Quote age", _age(quote_age))
            detail = {
                "bin": bin_labels[selected_bin],
                "source": "CLOB finite-depth snapshot" if quote else "Unavailable",
                "quote_status": quote.get("status") if quote else "unavailable",
                "target_quantity": quote.get("target_quantity") if quote else None,
                "bid_vwap": quote.get("bid_vwap") if quote else None,
                "bid_depth": quote.get("bid_depth") if quote else None,
                "requested_at": (
                    _format_timestamp(quote.get("requested_at"), display_zone)
                    if quote and quote.get("requested_at") is not None
                    else "Unavailable"
                ),
                "quote_received_at": (
                    _format_timestamp(quote_received, display_zone)
                    if quote_received is not None
                    else "Unavailable"
                ),
                "request_latency_ms": (
                    round(quote_latency_ms, 1) if quote_latency_ms is not None else None
                ),
                "market_id": quote.get("market_id") if quote else None,
                "token_id": quote.get("token_id") if quote else None,
                "error": quote.get("error_reason") if quote else None,
            }
            st.dataframe(pd.DataFrame([detail]), width="stretch", hide_index=True)
            levels = query.get_order_book(decision_id, selected_bin)
            if levels:
                st.subheader("Current finite depth")
                st.plotly_chart(depth_figure(levels), width="stretch")
                st.dataframe(pd.DataFrame(levels), width="stretch", hide_index=True)
            else:
                st.warning("Order book is empty or unavailable for the selected decision.")

    with paper_tab:
        st.button("Refresh", icon=":material/refresh:", key="refresh-paper")
        account = paper["account"]
        if account:
            metrics = st.columns(6)
            for column, key in zip(
                metrics,
                ("cash", "used_notional", "realized_pnl", "unrealized_pnl", "total_pnl", "nav"),
                strict=True,
            ):
                column.metric(key.replace("_", " ").title(), f"${account[key]:.2f}")
            st.dataframe(
                pd.DataFrame(_localize_records(list(account["positions"].values()), display_zone)),
                width="stretch",
            )
            scenario = account["scenario_pnl"]
            scenario_labels = {str(item["bin_id"]): str(item["label"]) for item in outcomes}
            if scenario:
                most_likely = max(outcomes, key=lambda item: item["model_probability"])["bin_id"]
                worst = min(scenario, key=scenario.get)
                best = max(scenario, key=scenario.get)
                colors = [
                    "#ffbf00"
                    if key == most_likely
                    else "#d62728"
                    if key == worst
                    else "#60A5FA"
                    if key == best
                    else "#7f7f7f"
                    for key in scenario
                ]
                figure = go.Figure(
                    go.Bar(
                        x=[scenario_labels[key] for key in scenario],
                        y=list(scenario.values()),
                        marker_color=colors,
                        text=[
                            "most likely"
                            if key == most_likely
                            else "worst"
                            if key == worst
                            else "best"
                            if key == best
                            else ""
                            for key in scenario
                        ],
                    )
                )
                figure.update_layout(
                    xaxis_title="Final settlement bin", yaxis_title="Final portfolio P&L ($)"
                )
                st.plotly_chart(figure, width="stretch")
            else:
                st.info("Scenario P&L is unavailable until contract bins are parsed.")
        else:
            st.info("Paper account snapshot is unavailable.")
        st.subheader("Orders")
        st.dataframe(
            pd.DataFrame(_localize_records(paper["orders"], display_zone)), width="stretch"
        )
        st.subheader("Fills")
        st.dataframe(pd.DataFrame(_localize_records(paper["fills"], display_zone)), width="stretch")

    with system_tab:
        st.button("Refresh", icon=":material/refresh:", key="refresh-system")
        st.subheader("Data health and runner heartbeat")
        git_sha = os.environ.get("NICE_WEATHER_GIT_SHA", "unknown")
        st.caption(
            f"Build {git_sha} · model {summary['model_version']} · "
            f"rule {summary['rule_version']} · time zone {timezone_name} · database {db.resolve()}"
        )
        st.dataframe(
            pd.DataFrame(_localize_records(health["checks"], display_zone)), width="stretch"
        )
        st.json(
            _localize_record(health["heartbeat"], display_zone)
            if health["heartbeat"]
            else {"status": "missing"}
        )
        st.subheader("Decision log")
        decisions = query.list_decisions()
        st.dataframe(pd.DataFrame(_localize_records(decisions, display_zone)), width="stretch")
        selected_decision = st.selectbox(
            "Decision trace", [item["decision_id"] for item in decisions], key="trace-decision"
        )
        st.dataframe(
            pd.DataFrame(
                _localize_records(query.get_decision_trace(selected_decision), display_zone)
            ),
            width="stretch",
        )
        st.subheader("Recent system events")
        st.dataframe(
            pd.DataFrame(_localize_records(health["events"], display_zone)), width="stretch"
        )


def main() -> None:
    args = _arguments()
    st.set_page_config(page_title="Nice Weather · NYC/KLGA", layout="wide")
    st.markdown(_DASHBOARD_STYLE, unsafe_allow_html=True)
    st.title("Polymarket NYC / KLGA Trader Dashboard")

    _render(args.db)


if __name__ == "__main__":
    main()

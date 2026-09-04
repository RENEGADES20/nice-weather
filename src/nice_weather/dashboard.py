from __future__ import annotations

import argparse
import math
import os
import uuid
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from html import escape
from pathlib import Path
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


def _epoch(value: object) -> float:
    parsed = _display_datetime(value, UTC)
    if parsed is None:
        raise ValueError(f"Invalid timestamp: {value}")
    return parsed.timestamp()


def _points(rows: list[dict[str, Any]], time_key: str, value_key: str) -> list[dict[str, object]]:
    return [
        {
            "time": _epoch(row[time_key]),
            "value": float(row[value_key]),
            "object_time": row[time_key],
            "received_at": row.get("received_at"),
        }
        for row in rows
        if row.get(time_key) is not None and row.get(value_key) is not None
    ]


def _last_trade_updates(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous: float | None = None
    ordered = sorted(ticks, key=lambda item: (_epoch(item["exchange_event_at"]), item["tick_id"]))
    for tick in ordered:
        value = tick.get("last_trade_price")
        if value is None:
            continue
        current = float(value)
        if previous is None or current != previous:
            result.append(tick)
        previous = current
    return result


def _select_price(ticks: list[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    cutoff = at.astimezone(UTC).timestamp()
    eligible = [item for item in ticks if _epoch(item["exchange_event_at"]) <= cutoff]
    clob = [item for item in eligible if item.get("source") == "clob_ws"]
    if clob:
        latest = max(clob, key=lambda item: (_epoch(item["exchange_event_at"]), item["tick_id"]))
        bid, ask, mid = latest.get("best_bid"), latest.get("best_ask"), latest.get("mid")
        if (
            latest.get("status") in {"available", "reconnect_snapshot"}
            and bid is not None
            and ask is not None
            and float(bid) <= float(ask)
            and mid is not None
        ):
            return {
                "value": float(mid),
                "source": "CLOB mid",
                "time": latest["exchange_event_at"],
                "received_at": latest.get("received_at"),
                "age_seconds": max(0.0, cutoff - _epoch(latest["exchange_event_at"])),
            }
    trades = _last_trade_updates(eligible)
    if trades:
        latest = trades[-1]
        age = cutoff - _epoch(latest["exchange_event_at"])
        if age <= 300:
            return {
                "value": float(latest["last_trade_price"]),
                "source": "Last trade",
                "time": latest["exchange_event_at"],
                "received_at": latest.get("received_at"),
                "age_seconds": max(0.0, age),
            }
    gamma = [
        item
        for item in eligible
        if item.get("source") == "gamma_fallback"
        and item.get("mid") is not None
        and item.get("status") not in {"crossed", "disconnect", "missing"}
    ]
    if gamma:
        latest = max(gamma, key=lambda item: (_epoch(item["exchange_event_at"]), item["tick_id"]))
        age = cutoff - _epoch(latest["exchange_event_at"])
        if age <= 600:
            return {
                "value": float(latest["mid"]),
                "source": "Gamma approximate",
                "time": latest["exchange_event_at"],
                "received_at": latest.get("received_at"),
                "age_seconds": max(0.0, age),
            }
    return None


def _price_points(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    previous: tuple[float, str] | None = None
    ordered = sorted(ticks, key=lambda item: (_epoch(item["exchange_event_at"]), item["tick_id"]))
    latest_clob: dict[str, Any] | None = None
    latest_trade: dict[str, Any] | None = None
    latest_gamma: dict[str, Any] | None = None
    for tick in ordered:
        if tick.get("source") == "clob_ws":
            latest_clob = tick
        if tick.get("last_trade_price") is not None:
            latest_trade = tick
        if (
            tick.get("source") == "gamma_fallback"
            and tick.get("mid") is not None
            and tick.get("status") not in {"crossed", "disconnect", "missing"}
        ):
            latest_gamma = tick
        at = _display_datetime(tick["exchange_event_at"], UTC)
        candidates = [item for item in (latest_clob, latest_trade, latest_gamma) if item]
        selected = _select_price(candidates, at) if at is not None else None
        if selected is None:
            if previous is not None:
                points.append(
                    {
                        "time": _epoch(tick["exchange_event_at"]),
                        "value": None,
                        "rawValue": None,
                        "rawUnit": "probability",
                        "priceSource": "Unavailable",
                        "received_at": tick.get("received_at"),
                    }
                )
                previous = None
            continue
        current = (selected["value"], selected["source"])
        if current == previous:
            continue
        points.append(
            {
                "time": _epoch(tick["exchange_event_at"]),
                "value": selected["value"],
                "rawValue": selected["value"],
                "rawUnit": "probability",
                "priceSource": selected["source"],
                "received_at": selected.get("received_at"),
            }
        )
        previous = current
    return points


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
    result.append(
        {
            "id": "price",
            "name": "Price",
            "description": "Selected-bin price using CLOB mid, recent trade, then Gamma fallback.",
            "group": "Market",
            "pane": "market",
            "format": "probability",
            "fill": "price",
            "color": "#2563EB",
            "lineStyle": "solid",
            "points": _price_points(ticks),
            "currentPrice": _select_price(ticks, now),
        }
    )
    return result


def _timeline_data(
    db: Path,
    event_id: str,
    object_day: date,
    object_timezone: str,
    horizon: int,
    selected_bin_id: str,
    visible_sources: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], datetime, datetime]:
    query = DashboardQuery(db)
    now = datetime.now(UTC)
    zone = ZoneInfo(object_timezone)
    start = datetime.combine(object_day, time.min, zone).astimezone(UTC)
    end = (datetime.combine(object_day, time.min, zone) + timedelta(days=horizon)).astimezone(UTC)
    timeline = query.get_weather_timeline(object_day, horizon, now, object_timezone)
    market = query.get_market_bin_history(event_id, [selected_bin_id], start, end)
    config = load_city_config()
    series = _timeline_series(
        timeline,
        market["ticks"],
        visible_sources,
        config.freshness.observation_age_seconds,
        now,
    )
    market_day_end = (datetime.combine(object_day, time.min, zone) + timedelta(days=1)).astimezone(
        UTC
    )
    for item in series:
        if item["id"] == "weather-gov":
            item["validTo"] = market_day_end.timestamp()
    revisions = query.get_forecast_revision_events(object_day, now, object_timezone)
    events = [
        {
            "id": f"forecast:{item['capture_id']}",
            "type": "forecast_revised",
            "time": _epoch(item["received_at"]),
            "title": f"Forecast revised to {float(item['forecast_tmax_f']):.0f}°F",
        }
        for item in revisions
    ]
    return series, events, start, end


def _series_delta(
    series: list[dict[str, Any]], previous: dict[str, dict[str, str]]
) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    delta: list[dict[str, Any]] = []
    current: dict[str, dict[str, str]] = {}
    for item in series:
        series_id = str(item["id"])
        prior = previous.get(series_id, {})
        hashes = {str(point["time"]): content_hash(point) for point in item["points"]}
        changed = [
            point
            for point in item["points"]
            if prior.get(str(point["time"])) != hashes[str(point["time"])]
        ]
        delta.append({**item, "points": changed})
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
    reference_source_id: str,
    channel_id: str,
    signature: str,
) -> None:
    try:
        series, events, start, end = _timeline_data(
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
        delta, hashes = _series_delta(series, previous)
        st.session_state["_repricing_feed_signature"] = signature
        st.session_state["_repricing_feed_hashes"] = hashes
        revision = content_hash(
            [(item["id"], item["points"][-1] if item["points"] else None) for item in delta]
        )
        trading_chart_feed(
            {
                "mode": "feed",
                "channelId": channel_id,
                "revision": revision,
                "selectedBinId": selected_bin_id,
                "referenceSeriesId": reference_source_id,
                "windowStart": start.timestamp(),
                "windowEnd": end.timestamp(),
                "series": delta,
                "events": events,
            },
            key=f"klga-repricing-feed-{channel_id}",
        )
    except Exception:
        return


def _render_trading_timeline(db: Path) -> None:
    query = DashboardQuery(db)
    days = query.list_market_days()
    if not days:
        st.info("No repricing market day is available.")
        return
    st.subheader("KLGA Tmax and market repricing")
    day_labels = {f"{item['local_day']} · {item['event_title']}": item for item in days}
    controls = st.columns((2.2, 1, 3.2, 3.2))
    selected_label = controls[0].selectbox("Market day", list(day_labels), key="market_day")
    selected_day = day_labels[selected_label]
    horizon = int(
        controls[1].selectbox(
            "Range", (1, 2, 3, 5), index=1, format_func=lambda value: f"{value}D", key="range"
        )
    )
    event_id = str(selected_day["event_id"])
    object_day = date.fromisoformat(str(selected_day["local_day"]))
    object_timezone = str(selected_day["timezone"])
    bins = query.get_event_bins(event_id)
    running = query.get_weather_timeline(object_day, 1, datetime.now(UTC), object_timezone)[
        "running_tmax"
    ]
    latest_tmax = float(running[-1]["temperature_f"]) if running else None
    default_bin = _default_timeline_bin(
        bins, latest_tmax, query.get_latest_event_probabilities(event_id)
    )
    bin_ids = [str(item["bin_id"]) for item in bins]
    if st.session_state.get("selected_bin_id") not in bin_ids:
        st.session_state["selected_bin_id"] = default_bin
    selected_bin_id = str(
        controls[2].radio(
            "Temperature interval",
            bin_ids,
            format_func=lambda value: next(
                str(item["label"]) for item in bins if item["bin_id"] == value
            ),
            horizontal=True,
            key="selected_bin_id",
        )
    )
    source_ids = list(_SOURCE_SPECS)
    selected_sources = controls[3].multiselect(
        "Weather data sources",
        source_ids,
        default=list(_DEFAULT_SOURCES),
        format_func=lambda value: str(_SOURCE_SPECS[value]["name"]),
        key="visible_source_ids",
    )
    visible_ids = [*selected_sources, "price"]
    reference = str(st.session_state.get("reference_source_id", "forecast"))
    if reference not in visible_ids:
        reference = "forecast" if "forecast" in visible_ids else visible_ids[0]
    st.session_state["reference_source_id"] = reference
    channel_id = st.session_state.setdefault("_repricing_channel_id", uuid.uuid4().hex)
    series, events, start, end = _timeline_data(
        db, event_id, object_day, object_timezone, horizon, selected_bin_id, selected_sources
    )
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
    payload = {
        "mode": "full",
        "channelId": channel_id,
        "revision": content_hash(series),
        "signature": signature,
        "timezone": "America/New_York",
        "selectedBinId": selected_bin_id,
        "referenceSeriesId": reference,
        "windowStart": start.timestamp(),
        "windowEnd": end.timestamp(),
        "series": series,
        "events": events,
    }
    if st.session_state.get("_repricing_feed_signature") != signature:
        _, hashes = _series_delta(series, {})
        st.session_state["_repricing_feed_signature"] = signature
        st.session_state["_repricing_feed_hashes"] = hashes
    component_state = trading_chart(payload, key="klga-tmax-trading-chart-v3")
    if isinstance(component_state, dict):
        candidate = component_state.get("referenceSeriesId")
        if candidate in visible_ids:
            st.session_state["reference_source_id"] = candidate
    _render_repricing_feed(
        db,
        event_id,
        object_day.isoformat(),
        object_timezone,
        horizon,
        selected_bin_id,
        tuple(selected_sources),
        str(st.session_state["reference_source_id"]),
        channel_id,
        signature,
    )
    st.caption(
        "Difference shows relative co-movement and divergence only; it does not establish "
        "causality "
        "or a true response delay and is excluded from trading decisions and historical labels."
    )


def _render(db: Path) -> None:
    query = DashboardQuery(db)
    try:
        summary = query.get_latest_decision_summary()
    except Exception as exc:
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

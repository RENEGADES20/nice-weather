from __future__ import annotations

import argparse
import os
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from astral import LocationInfo
from astral.sun import sun

from nice_weather.config import load_city_config
from nice_weather.domain import content_hash
from nice_weather.queries import DashboardQuery
from nice_weather.trading_chart import trading_chart

_TIMESTAMP_KEYS = frozenset({"decision_time", "source_time", "valid_from", "valid_to"})


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
        name="Model probability", x=labels, y=[item["model_probability"] for item in outcomes]
    )
    figure.add_scatter(
        name="Best Bid", x=labels, y=[item["best_bid"] for item in outcomes], mode="lines+markers"
    )
    figure.add_scatter(
        name="Best Ask", x=labels, y=[item["best_ask"] for item in outcomes], mode="lines+markers"
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
    for side, color in (("bid", "#2ca02c"), ("ask", "#d62728")):
        selected = [item for item in levels if item["side"] == side]
        figure.add_bar(
            name=side.title(),
            y=[str(item["price"]) for item in selected],
            x=[item["size"] for item in selected],
            orientation="h",
            marker_color=color,
        )
    figure.update_layout(
        barmode="group", xaxis_title="Displayed token quantity", yaxis_title="Price ($)"
    )
    return figure


def _money(value: object, decimals: int = 3) -> str:
    return f"${float(value):.{decimals}f}" if value is not None else "Unavailable"


def _display_timezone(timezone_name: str | None) -> tuple[tzinfo, str]:
    if timezone_name:
        try:
            return ZoneInfo(timezone_name), timezone_name
        except ZoneInfoNotFoundError:
            pass
    system_zone = datetime.now().astimezone().tzinfo or UTC
    return system_zone, getattr(system_zone, "key", None) or str(system_zone)


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
    return parsed.strftime("%F %T %Z") if parsed is not None else str(value)


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


def _points(
    rows: list[dict[str, Any]], time_key: str, value_key: str
) -> list[dict[str, object]]:
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


def _market_series(
    ticks: list[dict[str, Any]], bins: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    palette = ("#2dd4bf", "#60a5fa", "#f59e0b", "#f472b6", "#a78bfa", "#84cc16")
    result = []
    for index, contract_bin in enumerate(bins):
        bin_ticks = [item for item in ticks if item["bin_id"] == contract_bin["bin_id"]]
        label = str(contract_bin["label"])
        color = palette[index % len(palette)]
        clob = [item for item in bin_ticks if item["source"] == "clob_ws"]
        gamma = [item for item in bin_ticks if item["source"] == "gamma_fallback"]
        for key, name, style, default in (
            ("mid", "Mid", "solid", True),
            ("best_bid", "Bid", "dotted", True),
            ("best_ask", "Ask", "dashed", True),
            ("last_trade_price", "Last", "solid", False),
        ):
            result.append(
                {
                    "id": f"{contract_bin['bin_id']}:{key}",
                    "name": f"{label} {name}",
                    "group": "Market",
                    "axis": "right",
                    "color": color,
                    "lineStyle": style,
                    "defaultVisible": default,
                    "points": _points(clob, "exchange_event_at", key),
                }
            )
        result.append(
            {
                "id": f"{contract_bin['bin_id']}:gamma",
                "name": f"{label} Gamma fallback",
                "group": "Market",
                "axis": "right",
                "color": color,
                "lineStyle": "dashed",
                "defaultVisible": False,
                "points": _points(gamma, "exchange_event_at", "mid"),
            }
        )
    return result


def _render_trading_timeline(db: Path) -> None:
    query = DashboardQuery(db)
    days = query.list_market_days()
    if not days:
        return
    st.subheader("KLGA Tmax and market repricing")
    controls = st.columns((2, 3, 4, 3))
    day_labels = {
        f"{item['local_day']} · {item['event_title']}": item for item in days
    }
    selected_day_label = controls[0].selectbox(
        "Market day", list(day_labels), key="timeline-market-day"
    )
    selected_day = day_labels[selected_day_label]
    horizon = controls[1].selectbox(
        "Forecast range", options=(1, 2, 3, 5), index=1,
        format_func=lambda value: f"{value}D"
    )
    bins = query.get_event_bins(str(selected_day["event_id"]))
    middle = max(0, len(bins) // 2 - 1)
    default_bins = [str(item["bin_id"]) for item in bins[middle : middle + 3]]
    selected_bin_ids = controls[2].multiselect(
        "Temperature bins",
        options=[str(item["bin_id"]) for item in bins],
        default=default_bins,
        max_selections=6,
        format_func=lambda value: next(
            str(item["label"]) for item in bins if item["bin_id"] == value
        ),
    )
    threshold = controls[3].selectbox(
        "Price-in", options=(0.8, 0.9, 0.95, 0.99), index=1,
        format_func=lambda value: f"{value:.0%}",
    )
    if not selected_bin_ids:
        st.info("Select at least one temperature bin.")
        return
    horizon = int(horizon or 2)
    threshold = float(threshold or 0.9)
    object_day = date.fromisoformat(str(selected_day["local_day"]))
    object_timezone = str(selected_day["timezone"])
    zone = ZoneInfo(object_timezone)
    start = datetime.combine(object_day, time.min, zone).astimezone(UTC)
    end = (datetime.combine(object_day, time.min, zone) + timedelta(days=horizon)).astimezone(UTC)
    now = datetime.now(UTC)
    timeline = query.get_weather_timeline(object_day, horizon, now, object_timezone)
    selected_bins = [item for item in bins if str(item["bin_id"]) in selected_bin_ids]

    cache_key = content_hash(
        {
            "event": selected_day["event_id"],
            "bins": selected_bin_ids,
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
    )
    state_key = "timeline-market-cache"
    cached = st.session_state.get(state_key)
    if not isinstance(cached, dict) or cached.get("key") != cache_key:
        market = query.get_market_bin_history(
            str(selected_day["event_id"]), selected_bin_ids, start, end
        )
        cached = {"key": cache_key, "ticks": market["ticks"], "cursor": market["cursor"]}
    else:
        market = query.get_market_bin_history(
            str(selected_day["event_id"]),
            selected_bin_ids,
            start,
            end,
            cached.get("cursor"),
        )
        known = {str(item["tick_id"]) for item in cached["ticks"]}
        cached["ticks"].extend(
            item for item in market["ticks"] if str(item["tick_id"]) not in known
        )
        cached["cursor"] = market["cursor"]
    st.session_state[state_key] = cached

    metar = [item for item in timeline["observations"] if item["source"] == "aviationweather"]
    nws = [item for item in timeline["observations"] if item["source"] == "nws"]
    running = query.get_tmax_knowledge_events(object_day, now)
    series = [
        {
            "id": "forecast",
            "name": "NWS forecast",
            "group": "Weather",
            "axis": "left",
            "color": "#f2c94c",
            "lineStyle": "dashed",
            "defaultVisible": True,
            "points": _points(timeline["forecasts"], "valid_at", "temperature_f"),
        },
        {
            "id": "metar",
            "name": "METAR",
            "group": "Weather",
            "axis": "left",
            "color": "#ef6c5b",
            "lineStyle": "solid",
            "defaultVisible": True,
            "points": _points(metar, "observed_at", "temperature_f"),
        },
        {
            "id": "nws-observations",
            "name": "NWS observations",
            "group": "Weather",
            "axis": "left",
            "color": "#8b9dc3",
            "lineStyle": "dotted",
            "defaultVisible": False,
            "points": _points(nws, "observed_at", "temperature_f"),
        },
        {
            "id": "running-tmax",
            "name": "Running Tmax",
            "group": "Weather",
            "axis": "left",
            "color": "#f2f5f8",
            "lineStyle": "solid",
            "defaultVisible": True,
            "points": _points(running, "observed_at", "running_tmax_f"),
        },
        *_market_series(cached["ticks"], selected_bins),
    ]
    analysis = query.get_price_in_analysis(str(selected_day["event_id"]), threshold, 10.0)
    events = [
        {
            "id": f"{item['type']}:{item['bin_id']}:{item['object_time']}",
            "type": item["type"],
            "title": (
                f"{item['label']} eliminated"
                if item["type"] == "bin_eliminated"
                else (
                    f"Forecast revised to {item['contract_temperature_f']:.0f} F"
                    if item["type"] == "forecast_revised"
                    else f"{item['label']} entered"
                )
            ),
            "time": _epoch(item["object_time"]),
            "object_time": item["object_time"],
            "received_at": item["system_received_at"],
            "first_market_move_at": item["first_market_move_at"],
            "source_latency_seconds": item["source_latency_seconds"],
            "tradable_lead_seconds": item["tradable_lead_seconds"],
            "threshold_times": item["threshold_times"],
            "bin_id": item["bin_id"],
            "temperature_f": item["temperature_f"],
        }
        for item in analysis
    ]
    config = load_city_config()
    location = LocationInfo(
        config.city_name, "US", object_timezone, config.latitude, config.longitude
    )
    solar = sun(location.observer, date=object_day, tzinfo=zone)
    for name, title in (("sunset", "Sunset"), ("dusk", "Civil twilight ends")):
        instant = solar[name].astimezone(UTC)
        events.append(
            {
                "id": f"solar:{name}:{object_day}",
                "type": name,
                "title": title,
                "time": round(instant.timestamp()),
                "object_time": instant.isoformat(),
                "received_at": instant.isoformat(),
            }
        )
    events.sort(key=lambda item: int(item["time"]))
    context = getattr(st, "context", None)
    _, display_timezone = _display_timezone(getattr(context, "timezone", None))
    payload = {
        "signature": cache_key,
        "objectTimezone": object_timezone,
        "displayTimezone": display_timezone,
        "threshold": str(threshold),
        "series": series,
        "events": events,
    }
    trading_chart(payload, key="klga-tmax-trading-chart-v2")
    st.caption(
        "Axis labels use the trader display time zone. Market-day assignment and all research "
        f"calculations remain fixed to {object_timezone}."
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
    display_zone, timezone_name = _display_timezone(getattr(context, "timezone", None))
    now = datetime.now(UTC)

    git_sha = os.environ.get("NICE_WEATHER_GIT_SHA", "unknown")
    st.caption(
        f"Decision {decision_id} · build {git_sha} · time zone {timezone_name} · "
        f"database {db.resolve()}"
    )
    top = st.columns(9)
    values = (
        ("Mode", summary["mode"]),
        ("City / station", f"{summary['city_code']} / {summary['station_id']}"),
        ("Market day", summary["local_day"]),
        ("Market", "CLOSED" if summary["event_closed"] else "OPEN"),
        (
            "Local time",
            _format_timestamp(now, display_zone),
        ),
        ("Decision time", _format_timestamp(summary["decision_time"], display_zone)),
        ("DataHealth", summary["health_level"]),
        ("Decision", summary["overall_action"]),
        ("Refreshed", now.astimezone(display_zone).strftime("%H:%M:%S %Z")),
    )
    for column, (label, value) in zip(top, values, strict=True):
        column.metric(label, value)
    if summary["reason_codes"]:
        st.warning("Reason codes: " + ", ".join(summary["reason_codes"]))

    overview, market_tab, paper_tab, system_tab = st.tabs(
        ["Overview", "Market Detail", "Paper", "System & Audit"]
    )
    with overview:
        with st.expander("Contract and settlement rules", expanded=False):
            st.markdown(f"[{summary['event_title']}]({summary['market_url']})")
            st.dataframe(
                pd.DataFrame([_localize_record(contract["contract"], display_zone)]),
                width="stretch",
            )
            st.dataframe(
                pd.DataFrame(_localize_records(contract["bins"], display_zone)), width="stretch"
            )
        st.subheader("Model probability and executable market prices")
        probability_sum = float(summary["probability_summary"]["probability_sum"])
        if abs(probability_sum - 1.0) > 1e-6:
            st.error(f"Probability sum invalid: {probability_sum:.9f}; candidates are blocked.")
        st.plotly_chart(probability_figure(outcomes), width="stretch")
        st.dataframe(pd.DataFrame(_localize_records(outcomes, display_zone)), width="stretch")
        st.subheader("KLGA observations and baseline Tmax")
        probability = summary["probability_summary"]
        weather_metrics = st.columns(6)
        observed = probability["observed_tmax_f"]
        weather_metrics[0].metric(
            "Official Hourly Tmax",
            f"{observed:.1f} °F" if observed is not None else "Unavailable",
        )
        weather_metrics[1].metric("NWS baseline Tmax", f"{probability['baseline_tmax_f']:.1f} °F")
        features = model_context["features"] or {}
        nws_tmax = features.get("nws_observed_tmax_f")
        metar_tmax = features.get("metar_observed_tmax_f")
        weather_metrics[2].metric(
            "NWS observed Tmax",
            f"{nws_tmax:.1f} °F" if nws_tmax is not None else "Unavailable",
        )
        weather_metrics[3].metric(
            "METAR Tmax",
            f"{metar_tmax:.1f} °F" if metar_tmax is not None else "Unavailable",
        )
        weather_metrics[4].metric("Model mean", f"{probability['mean_tmax_f']:.1f} °F")
        weather_metrics[5].metric(
            "80% interval",
            f"{probability['interval_low_f']:.1f}–{probability['interval_high_f']:.1f} °F",
        )
        if features:
            st.caption(
                f"Feature schema {features['feature_schema_version']} · "
                f"as-of {_format_timestamp(features['decision_time'], display_zone)} · "
                f"input {features['input_set_hash']}"
            )

    with market_tab:
        labels = {str(item["label"]): str(item["bin_id"]) for item in outcomes}
        if not labels:
            st.warning("No parsed temperature bins are available for this blocked decision.")
        else:
            selected_label = st.selectbox("Temperature bin", list(labels), key="market-bin")
            selected_bin = labels[selected_label]
            history = query.get_outcome_history(selected_bin)
            if history:
                history_figure = go.Figure()
                for column in ("best_bid", "best_ask", "model_probability", "net_edge"):
                    history_figure.add_scatter(
                        name=column,
                        x=[
                            _display_datetime(item["decision_time"], display_zone)
                            for item in history
                        ],
                        y=[item[column] for item in history],
                    )
                signal_points = [item for item in history if item["risk_approved"]]
                if signal_points:
                    history_figure.add_scatter(
                        name="Signal",
                        x=[
                            _display_datetime(item["decision_time"], display_zone)
                            for item in signal_points
                        ],
                        y=[item["model_probability"] for item in signal_points],
                        mode="markers",
                        marker={"symbol": "star", "size": 12},
                    )
                fill_points = [item for item in history if item["filled_at"]]
                if fill_points:
                    history_figure.add_scatter(
                        name="Paper fill",
                        x=[
                            _display_datetime(item["filled_at"], display_zone)
                            for item in fill_points
                        ],
                        y=[item["fill_price"] for item in fill_points],
                        mode="markers",
                        marker={"symbol": "x", "size": 11},
                    )
                st.plotly_chart(history_figure, width="stretch")
            else:
                st.info("No decision history for this bin.")
            levels = query.get_order_book(decision_id, selected_bin)
            if levels:
                selected = next(item for item in outcomes if item["bin_id"] == selected_bin)
                metrics = st.columns(4)
                metrics[0].metric("Best Bid", _money(selected["best_bid"]))
                metrics[1].metric("Best Ask", _money(selected["best_ask"]))
                metrics[2].metric("Executable VWAP", _money(selected["executable_price"]))
                metrics[3].metric("Executable qty", f"{selected['executable_quantity']:.2f}")
                st.plotly_chart(depth_figure(levels), width="stretch")
            else:
                st.warning("Order book is empty or unavailable for the selected decision.")

    with paper_tab:
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
                    else "#2ca02c"
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
        st.subheader("Data health and runner heartbeat")
        st.caption(
            f"Model version: {summary['model_version']} · Rule version: {summary['rule_version']}"
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
    st.title("Polymarket NYC / KLGA Trader Dashboard")

    @st.fragment(run_every=f"{max(5, min(15, args.refresh_seconds))}s")
    def refresh() -> None:
        _render(args.db)

    @st.fragment(run_every="2s")
    def refresh_trading_timeline() -> None:
        try:
            _render_trading_timeline(args.db)
        except Exception as exc:
            st.warning(f"Trading timeline unavailable: {type(exc).__name__}: {exc}")

    refresh()
    refresh_trading_timeline()


if __name__ == "__main__":
    main()

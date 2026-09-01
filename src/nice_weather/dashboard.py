from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nice_weather.queries import DashboardQuery


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
    weather: dict[str, list[dict[str, object]]], summary: dict[str, object]
) -> go.Figure:
    figure = go.Figure()
    observations = weather["observations"]
    forecasts = weather["forecasts"]
    if observations:
        figure.add_scatter(
            name="KLGA METAR observed",
            x=[item["observed_at"] for item in observations],
            y=[item["temperature_f"] for item in observations],
            customdata=[[item["received_at"], item["snapshot_id"]] for item in observations],
            hovertemplate="%{x}<br>%{y:.1f}°F<br>received %{customdata[0]}<extra></extra>",
        )
    if forecasts:
        figure.add_scatter(
            name="NWS hourly forecast",
            x=[item["valid_at"] for item in forecasts],
            y=[item["temperature_f"] for item in forecasts],
            line={"dash": "dot"},
            customdata=[[item["issued_at"], item["received_at"]] for item in forecasts],
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
    figure.update_layout(yaxis_title="Temperature (°F)", xaxis_title="Observation / valid time")
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
    weather = query.get_weather_path(decision_id)
    health = query.get_health_view(decision_id)
    paper = query.get_paper_view(decision_id)
    model_context = query.get_model_context(decision_id)

    git_sha = os.environ.get("NICE_WEATHER_GIT_SHA", "unknown")
    st.caption(f"Decision {decision_id} · build {git_sha} · database {db.resolve()}")
    top = st.columns(9)
    values = (
        ("Mode", summary["mode"]),
        ("City / station", f"{summary['city_code']} / {summary['station_id']}"),
        ("Market day", summary["local_day"]),
        ("Market", "CLOSED" if summary["event_closed"] else "OPEN"),
        (
            "NY time",
            datetime.now(UTC).astimezone(ZoneInfo("America/New_York")).strftime("%F %T %Z"),
        ),
        ("Decision time", summary["decision_time"]),
        ("DataHealth", summary["health_level"]),
        ("Decision", summary["overall_action"]),
        ("Refreshed", datetime.now(UTC).strftime("%H:%M:%S UTC")),
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
            st.dataframe(pd.DataFrame([contract["contract"]]), width="stretch")
            st.dataframe(pd.DataFrame(contract["bins"]), width="stretch")
        st.subheader("Model probability and executable market prices")
        probability_sum = float(summary["probability_summary"]["probability_sum"])
        if abs(probability_sum - 1.0) > 1e-6:
            st.error(f"Probability sum invalid: {probability_sum:.9f}; candidates are blocked.")
        st.plotly_chart(probability_figure(outcomes), width="stretch")
        st.dataframe(pd.DataFrame(outcomes), width="stretch")
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
        st.plotly_chart(weather_figure(weather, summary), width="stretch")
        if features:
            st.caption(
                f"Feature schema {features['feature_schema_version']} · "
                f"as-of {features['decision_time']} · input {features['input_set_hash']}"
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
                        x=[item["decision_time"] for item in history],
                        y=[item[column] for item in history],
                    )
                signal_points = [item for item in history if item["risk_approved"]]
                if signal_points:
                    history_figure.add_scatter(
                        name="Signal",
                        x=[item["decision_time"] for item in signal_points],
                        y=[item["model_probability"] for item in signal_points],
                        mode="markers",
                        marker={"symbol": "star", "size": 12},
                    )
                fill_points = [item for item in history if item["filled_at"]]
                if fill_points:
                    history_figure.add_scatter(
                        name="Paper fill",
                        x=[item["filled_at"] for item in fill_points],
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
                pd.DataFrame(list(account["positions"].values())), width="stretch"
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
        st.dataframe(pd.DataFrame(paper["orders"]), width="stretch")
        st.subheader("Fills")
        st.dataframe(pd.DataFrame(paper["fills"]), width="stretch")

    with system_tab:
        st.subheader("Data health and runner heartbeat")
        st.caption(
            f"Model version: {summary['model_version']} · Rule version: {summary['rule_version']}"
        )
        st.dataframe(pd.DataFrame(health["checks"]), width="stretch")
        st.json(health["heartbeat"] or {"status": "missing"})
        st.subheader("Decision log")
        decisions = query.list_decisions()
        st.dataframe(pd.DataFrame(decisions), width="stretch")
        selected_decision = st.selectbox(
            "Decision trace", [item["decision_id"] for item in decisions], key="trace-decision"
        )
        st.dataframe(
            pd.DataFrame(query.get_decision_trace(selected_decision)), width="stretch"
        )
        st.subheader("Recent system events")
        st.dataframe(pd.DataFrame(health["events"]), width="stretch")


def main() -> None:
    args = _arguments()
    st.set_page_config(page_title="Nice Weather · NYC/KLGA", layout="wide")
    st.title("Polymarket NYC / KLGA Trader Dashboard")

    @st.fragment(run_every=f"{max(5, min(15, args.refresh_seconds))}s")
    def refresh() -> None:
        _render(args.db)

    refresh()


if __name__ == "__main__":
    main()

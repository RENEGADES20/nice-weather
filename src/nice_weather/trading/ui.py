from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from nice_weather.trading import STRATEGIES, validate_strategy
from nice_weather.trading.dataset import coverage, load_dataset
from nice_weather.trading.metrics import daily_statistics
from nice_weather.trading.storage import Requests, connect, digest


def read_rows(path, sql, parameters=()):
    if not path.exists():
        return []
    with connect(path, readonly=True) as con:
        return [dict(r) for r in con.execute(sql, parameters)]


def submit(root, account, mode, kind, payload, key):
    fingerprint = digest([account, mode, kind, payload])
    state_key = f"request-{key}-{fingerprint}"
    request_id = st.session_state.setdefault(state_key, str(uuid.uuid4()))
    Requests(root / "requests" / "requests.sqlite3").submit(
        request_id, account, mode, kind, payload, ttl=86400 if mode == "backtest" else 60
    )
    st.success(f"Request queued: {request_id}")


def metrics(snapshot):
    columns = st.columns(4)
    for i, (key, label) in enumerate(
        (
            ("cash", "Cash"),
            ("available", "Available"),
            ("reserved", "Reserved"),
            ("equity", "Equity"),
            ("realized_pnl", "Realized P&L"),
            ("unrealized_pnl", "Unrealized P&L"),
            ("total_pnl", "Total P&L"),
            ("fees", "Fees"),
            ("market_value", "Position value"),
            ("drawdown", "Drawdown"),
            ("max_drawdown", "Max drawdown"),
            ("trade_count", "Fills"),
        )
    ):
        value = snapshot.get(key)
        text = (
            "Unavailable"
            if value is None
            else (f"{value:.2%}" if "drawdown" in key else f"{value:,.4f}")
        )
        columns[i % 4].metric(label, text)


def result_view(root, run, *, show_metrics=True):
    snapshot = json.loads(run["snapshot"]) if run["snapshot"] else None
    st.caption(f"{run['mode'].upper()} · {run['account']} · {run['run_id']} · {run['status']}")
    if run["status"] != "completed" and run["mode"] == "backtest":
        st.warning("Incomplete result; do not treat as a completed backtest.")
    if run["error"]:
        st.error(run["error"])
    if not snapshot:
        st.info("Waiting for the first engine snapshot.")
        return
    if show_metrics:
        metrics(snapshot)
    series = read_rows(
        root / "results.sqlite3",
        "SELECT ts,equity,drawdown FROM equity WHERE run_id=? ORDER BY seq",
        (run["run_id"],),
    )
    if series:
        frame = pd.DataFrame(series)
        frame["time"] = pd.to_datetime(frame.ts, unit="ns", utc=True)
        values = frame[["equity", "drawdown"]]
        changes = values.ne(values.shift()).any(axis=1) | values.ne(values.shift(-1)).any(axis=1)
        plotted = frame.loc[changes]
        for field, title in (("equity", "Equity · pUSD"), ("drawdown", "Drawdown")):
            figure = go.Figure(
                go.Scatter(
                    x=plotted.time,
                    y=plotted[field],
                    mode="lines",
                    line_shape="hv",
                    connectgaps=False,
                )
            )
            figure.update_layout(title=title, height=250, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(figure, width="stretch", key=f"{run['run_id']}-{field}")
        statistics = daily_statistics(series)
        snapshot["sharpe"] = statistics["sharpe"]
        st.dataframe(pd.DataFrame(statistics["days"]), width="stretch")
    for key, label in (
        ("positions", "Positions · Bid valuation"),
        ("orders", "Orders"),
        ("fills", "Fills"),
        ("rejections", "Rejected requests"),
    ):
        with st.expander(label, expanded=key == "positions"):
            st.dataframe(pd.DataFrame(snapshot[key]), width="stretch")
    if snapshot["positions"]:
        scenarios = []
        for market in snapshot["markets"]:
            if market["outcome"] != "YES":
                continue
            same_day = [p for p in snapshot["positions"] if p["date"] == market["date"]]
            payout = sum(
                p["quantity"]
                * int((p["condition"] == market["condition"]) == (p["outcome"] == "YES"))
                for p in same_day
            )
            scenarios.append(
                {
                    "date": market["date"],
                    "final_bin": market["bin"],
                    "position_payout": payout,
                    "position_pnl_before_fees": payout - sum(p["cost"] for p in same_day),
                }
            )
        st.caption("Joint YES/NO scenarios assume exactly one final temperature bin.")
        st.dataframe(pd.DataFrame(scenarios), width="stretch")
    st.caption(snapshot["coverage"])
    st.write({k: snapshot.get(k) for k in ("win_rate", "profit_loss_ratio", "sharpe")})
    with st.expander("Audit and exports"):
        st.json(json.loads(run["config"]))
        st.download_button(
            "Export JSON",
            json.dumps(run | {"snapshot": snapshot}, indent=2),
            f"{run['run_id']}.json",
            "application/json",
            key=f"json-{run['run_id']}",
        )
        st.download_button(
            "Export fills CSV",
            pd.DataFrame(snapshot["fills"]).to_csv(index=False),
            f"{run['run_id']}-fills.csv",
            "text/csv",
            key=f"csv-{run['run_id']}",
        )


def trading(root):
    mode = st.radio("Account mode", ["SANDBOX", "LIVE"], horizontal=True, key="trading-mode")
    st.button("Refresh trading", key="trading-refresh")
    if mode == "LIVE":
        st.info("LIVE · Disconnected · Real order execution disabled · No credentials loaded")
        st.metric("Live equity", "Unavailable")
        return
    runs = read_rows(root / "results.sqlite3", "SELECT * FROM runs WHERE mode='sandbox'")
    if not runs:
        st.info("SANDBOX · Worker not connected. Start the trading worker to create the account.")
        st.code(
            "nice-weather trading run --mode sandbox --db <weather.sqlite3> --root <trading-root>"
        )
        return
    account = st.selectbox("Trading account", [r["account"] for r in runs])
    run = next(r for r in runs if r["account"] == account)
    snapshot = json.loads(run["snapshot"]) if run["snapshot"] else {}
    state_key = f"strategy-state-{account}"
    enabled = snapshot.get("strategy_enabled", False)
    previous, generation = st.session_state.get(state_key, (enabled, 0))
    if previous != enabled:
        generation += 1
    st.session_state[state_key] = (enabled, generation)
    connected = time.time() - run["updated"] < 10 and run["status"] == "running"
    st.info(
        f"SANDBOX · {account} · {'Connected' if connected else 'Disconnected / paused'} · "
        f"Strategy {'ON' if snapshot.get('strategy_enabled') else 'OFF'} · "
        f"Updated {pd.to_datetime(run['updated'], unit='s', utc=True)}"
    )
    if snapshot:
        metrics(snapshot)
    markets = snapshot.get("markets", [])
    if markets:
        by_token = {m["token"]: m for m in markets}
        with st.form("manual-order"):
            token = st.selectbox(
                "Token",
                list(by_token),
                format_func=lambda t: (
                    f"{by_token[t]['date']} | {by_token[t]['bin']} | {by_token[t]['outcome']} | {t}"
                ),
            )
            side = st.selectbox("Side", ["BUY", "SELL"])
            size_mode = st.selectbox("Buy sizing", ["Shares", "Amount"])
            size = st.number_input(
                "Quantity / amount", min_value=0.000001, value=1.0, format="%.6f"
            )
            price = st.number_input(
                "Limit price", min_value=0.000001, max_value=0.999999, value=0.40, format="%.6f"
            )
            tif = st.selectbox("Time in force", ["GTC", "GTD", "IOC", "FOK"])
            expiry = st.text_input("GTD expiry (ISO timestamp with timezone)", "")
            post = st.checkbox("Post-only")
            preview = st.form_submit_button("Preview order", disabled=not connected)
        if preview:
            payload = {"token": token, "side": side, "price": price, "tif": tif, "post_only": post}
            payload["amount" if size_mode == "Amount" and side == "BUY" else "quantity"] = size
            if tif == "GTD":
                payload["expire_time"] = expiry
            st.session_state["order-preview"] = {"account": account, "payload": payload}
        draft = st.session_state.get("order-preview")
        if draft and draft["account"] == account:
            payload = draft["payload"]
            st.json({"account": account, "mode": "SANDBOX", **payload})
            m = by_token[payload["token"]]
            qty = payload.get("quantity", payload.get("amount", 0) / payload["price"])
            fee = (
                qty
                * m["fee_rate"]
                * (payload["price"] * (1 - payload["price"])) ** m["fee_exponent"]
            )
            st.caption(
                f"Estimated taker fee at limit: {fee:.6f}; worker revalidates before execution."
            )
            if st.button("Submit reviewed order", disabled=not connected):
                submit(root, account, "sandbox", "order", payload, "manual")
            if st.button("Clear preview / prepare a new order"):
                fingerprint = digest([account, "sandbox", "order", payload])
                st.session_state.pop(f"request-manual-{fingerprint}", None)
                st.session_state.pop("order-preview", None)
                st.rerun()
        with st.form("order-controls"):
            action = st.selectbox("Position / order action", ["cancel", "replace", "close"])
            order_id = st.selectbox(
                "Order", [o["order_id"] for o in snapshot.get("orders", [])] or [""]
            )
            exit_token = st.selectbox("Exit / replacement token", list(by_token))
            replace_side = st.selectbox("Replacement side", ["BUY", "SELL"])
            target = st.number_input(
                "Target total / exit shares (0 = full exit)", min_value=0.0, value=0.0
            )
            replacement_price = st.number_input(
                "Replacement limit", min_value=0.001, max_value=0.999, value=0.4, format="%.3f"
            )
            if st.form_submit_button("Queue action", disabled=not connected):
                payload = {"order_id": order_id, "token": exit_token}
                if action == "replace":
                    payload |= {
                        "side": replace_side,
                        "target_quantity": target,
                        "price": replacement_price,
                        "tif": "GTC",
                    }
                elif action == "close" and target:
                    payload["quantity"] = target
                submit(root, account, "sandbox", action, payload, "controls")
        if st.button("Prepare another position / order action"):
            for key in list(st.session_state):
                if key.startswith("request-controls-"):
                    del st.session_state[key]
            st.info("A new action will receive a new request ID.")
        with st.form("strategy-controls"):
            strategy = st.selectbox("Registered strategy", list(STRATEGIES), key="trading-strategy")
            strategy_tokens = st.multiselect("Strategy tokens", list(by_token))
            strategy_parameters = st.text_area("Trading strategy parameters (JSON)", "{}")
            st.caption(
                "Acceptance strategies are test utilities. "
                "Stop cancels strategy orders and keeps positions."
            )
            start = st.form_submit_button("Start strategy", disabled=not connected)
            stop = st.form_submit_button("Stop strategy", disabled=not connected)
            if start or stop:
                submit(
                    root,
                    account,
                    "sandbox",
                    "start" if start else "stop",
                    {
                        "strategy_id": strategy,
                        "tokens": strategy_tokens,
                        "parameters": validate_strategy(
                            strategy, json.loads(strategy_parameters), "sandbox"
                        )
                        if start
                        else {},
                    },
                    f"strategy-{generation}",
                )
    result_view(root, run, show_metrics=False)
    st.dataframe(
        pd.DataFrame(
            read_rows(
                root / "requests" / "requests.sqlite3",
                "SELECT * FROM requests WHERE account=? ORDER BY created DESC LIMIT 30",
                (account,),
            )
        ),
        width="stretch",
    )


def backtest(root):
    st.button("Refresh backtests", key="backtest-refresh")
    datasets = {}
    for path in sorted((root / "datasets").glob("*.json")):
        try:
            datasets[path.stem] = load_dataset(path)
        except (ValueError, KeyError) as exc:
            st.error(f"Dataset {path.name}: {exc}")
    if not datasets:
        st.info(
            "No immutable self-collected dataset available. Export a received-time window first."
        )
        st.code(
            "nice-weather backtest export --db <weather.sqlite3> --root <trading-root> "
            "--start <ISO-with-timezone> --end <ISO-with-timezone>"
        )
    else:
        dataset_id = st.selectbox(
            "Self-collected dataset",
            list(datasets),
            format_func=lambda d: f"{datasets[d]['start']} → {datasets[d]['end']} · {d[:12]}",
        )
        strategy = st.selectbox("Backtest strategy", list(STRATEGIES))
        dataset = datasets[dataset_id]
        labels = {}
        for event in dataset["events"]:
            if event["kind"] == "contract":
                row = event["data"]
                for side, key in (("YES", "yes_token_id"), ("NO", "no_token_id")):
                    labels[row[key]] = f"{row['local_day']} | {row['label']} | {side} | {row[key]}"
        tokens = st.multiselect(
            "Market YES / NO tokens",
            list(dataset["coverage"]["quote_counts"]),
            format_func=lambda token: labels.get(token, token),
        )
        parameters = st.text_area(
            "Strategy parameters (JSON)",
            json.dumps(STRATEGIES[strategy]["parameters"]),
            key=f"params-{strategy}",
        )
        cash = st.number_input("Initial cash", min_value=1.0, max_value=1_000_000.0, value=100.0)
        st.caption(
            "L1 top depth approximation · liquidity consumption · no maker advantage / rebate"
        )
        try:
            params = validate_strategy(strategy, json.loads(parameters), "backtest")
            check = coverage(dataset, tokens, params.get("require_both", False))
            for reason in check["rejection_reasons"]:
                st.warning(reason)
            st.caption(f"Selected tokens: {len(tokens)} · Input events: {len(dataset['events'])}")
            with st.expander("Data coverage and execution limitations"):
                st.json(check)
            if st.button("Queue backtest", disabled=bool(check["rejection_reasons"])):
                submit(
                    root,
                    "backtest-001",
                    "backtest",
                    "backtest",
                    {
                        "dataset_id": dataset_id,
                        "strategy_id": strategy,
                        "parameters": params,
                        "tokens": tokens,
                        "cash": cash,
                    },
                    "backtest",
                )
        except (ValueError, TypeError) as exc:
            st.error(str(exc))
    requests = read_rows(
        root / "requests" / "requests.sqlite3",
        "SELECT * FROM requests WHERE mode='backtest' ORDER BY created DESC LIMIT 50",
    )
    st.dataframe(pd.DataFrame(requests), width="stretch")
    pending = [r["request_id"] for r in requests if r["status"] in {"queued", "running"}]
    if pending:
        cancel = st.selectbox("Cancel pending run", pending)
        if st.button("Cancel selected run"):
            submit(root, "backtest-001", "backtest", "cancel_run", {"run_id": cancel}, "cancel-run")
    runs = read_rows(
        root / "results.sqlite3",
        "SELECT * FROM runs WHERE mode='backtest' ORDER BY updated DESC LIMIT 100",
    )
    if runs:
        selected = st.multiselect(
            "Inspect / compare up to two runs",
            [r["run_id"] for r in runs],
            max_selections=2,
            key="compare-runs",
        )
        for column, run_id in zip(st.columns(max(len(selected), 1)), selected, strict=False):
            run = next(r for r in runs if r["run_id"] == run_id)
            with column:
                result_view(root, run)
                if st.button("Rerun with same configuration", key=f"rerun-{run_id}"):
                    config = json.loads(run["config"])
                    Requests(root / "requests" / "requests.sqlite3").submit(
                        str(uuid.uuid4()),
                        "backtest-001",
                        "backtest",
                        "backtest",
                        {
                            k: config[k]
                            for k in ("dataset_id", "strategy_id", "parameters", "tokens", "cash")
                        },
                        ttl=86400,
                    )
                    st.success("Rerun queued")


def render(db: Path, trading_tab, backtest_tab):
    root = Path(os.environ.get("NICE_WEATHER_TRADING_ROOT", str(db.parent / "trading")))
    for tab, renderer in ((trading_tab, trading), (backtest_tab, backtest)):
        with tab:
            try:
                renderer(root)
            except Exception as exc:
                st.error(f"Workbench unavailable: {exc}")

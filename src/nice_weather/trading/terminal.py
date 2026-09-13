from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import streamlit as st

from nice_weather.trading.metrics import pnl_view
from nice_weather.trading.storage import Requests, connect
from nice_weather.trading_chart import _component


@st.cache_data(ttl=2, max_entries=32, show_spinner=False)
def rows(path, sql, parameters=()):
    if not path.exists():
        return []
    with connect(path, readonly=True) as con:
        return [dict(r) for r in con.execute(sql, parameters)]


@st.cache_data(ttl=30, max_entries=8, show_spinner=False)
def equity_history(path, run_id):
    with connect(path, readonly=True) as con:
        samples = []
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='paper_equity'").fetchone():
            samples = [
                dict(r)
                for r in con.execute(
                    "SELECT ts,equity,realized,fees FROM paper_equity WHERE run_id=? ORDER BY ts",
                    (run_id,),
                )
            ]
        boundary = samples[0]["ts"] if samples else 2**63 - 1
        legacy = [
            dict(r)
            for r in con.execute(
                "SELECT MAX(ts) ts, CASE WHEN COUNT(equity)=COUNT(*) THEN equity END equity "
                "FROM equity WHERE run_id=? AND ts<? GROUP BY ts/60000000000 ORDER BY ts",
                (run_id, boundary),
            )
        ]
    return legacy + samples


@st.cache_data(ttl=15, max_entries=16, show_spinner=False)
def price_history(path, token):
    with connect(path, readonly=True) as con:
        return [
            dict(r)
            for r in con.execute(
                "SELECT MAX(rowid) AS sample,received_at,mid FROM market_top_ticks "
                "WHERE token_id=? AND source='clob_ws' AND event_kind IN ('quote','snapshot') "
                "GROUP BY substr(received_at,1,16) ORDER BY received_at",
                (token,),
            )
        ]


@st.fragment(run_every="2s")
def terminal(root: Path, db: Path, mode: str, account: str):
    runs = rows(
        root / "results.sqlite3",
        "SELECT * FROM runs WHERE mode='sandbox' AND account=?",
        (account,),
    )
    run = runs[0] if runs else None
    snapshot = json.loads(run["snapshot"]) if run and run["snapshot"] else {}
    config = json.loads(run["config"]) if run else {"cash": 100}
    markets = snapshot.get("markets", [])
    token_key = f"terminal-token-{account}"
    token = st.session_state.get(token_key)
    if token not in {m["token"] for m in markets}:
        today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
        token = next(
            (m["token"] for m in markets if m["date"] >= today),
            markets[0]["token"] if markets else None,
        )
    selected = next((m for m in markets if m["token"] == token), None)
    pair = [m["token"] for m in markets if selected and m["condition"] == selected["condition"]]
    depth = {}
    try:
        with httpx.Client(timeout=1, trust_env=False) as client:
            result = client.get(
                f"http://127.0.0.1:{int(os.environ.get('NICE_WEATHER_DEPTH_PORT', '8766'))}/depth",
                params=[("token", t) for t in pair],
            )
            result.raise_for_status()
            depth = result.json()
    except (httpx.HTTPError, ValueError):
        pass
    samples = []
    if run:
        samples = equity_history(root / "results.sqlite3", run["run_id"])
        samples.append(
            {
                "ts": int(run["updated"] * 1e9),
                "equity": snapshot.get("equity"),
                "realized": snapshot.get("realized_pnl"),
                "fees": snapshot.get("fees"),
            }
        )
    performance = pnl_view(
        samples,
        config.get("cash", 100),
        snapshot.get("fills", []),
        config.get("started_ns", samples[0]["ts"] if samples else time.time_ns()),
    )
    history = []
    if token and db.exists():
        try:
            history = price_history(db, token)
        except Exception:
            pass
    notices = rows(
        root / "requests" / "requests.sqlite3",
        "SELECT kind,status,error,created FROM requests "
        "WHERE account=? ORDER BY created DESC LIMIT 3",
        (account,),
    )
    connected = bool(run and run["status"] == "running" and time.time() - run["updated"] < 10)
    payload = {
        "mode": "terminal",
        "account": account,
        "accountMode": mode,
        "connected": connected,
        "snapshot": snapshot if mode == "Paper" else {},
        "markets": markets,
        "selectedToken": token,
        "depth": depth,
        "performance": performance if mode == "Paper" else {"points": [], "days": []},
        "history": history,
        "notices": notices if mode == "Paper" else [],
        "updated": run["updated"] if run else None,
    }
    action = _component(
        payload=payload, height=1500, key=f"terminal-{mode}-{account}", default=None
    )
    if not isinstance(action, dict) or st.session_state.get("terminal-action") == action.get("id"):
        return
    st.session_state["terminal-action"] = action.get("id")
    if action.get("kind") == "select" and action.get("token") in {m["token"] for m in markets}:
        st.session_state[token_key] = action["token"]
        st.rerun(scope="fragment")
    elif (
        mode == "Paper"
        and connected
        and action.get("kind") in {"order", "cancel", "replace", "close", "start", "stop"}
    ):
        Requests(root / "requests" / "requests.sqlite3").submit(
            action["id"], account, "sandbox", action["kind"], action.get("payload", {})
        )
        st.rerun(scope="fragment")


def trading(root, db):
    controls = st.columns([1, 2])
    mode = controls[0].radio("Mode", ["Paper", "Live"], horizontal=True, key="terminal-mode")
    accounts = rows(
        root / "results.sqlite3", "SELECT account FROM runs WHERE mode='sandbox' ORDER BY account"
    )
    choices = [r["account"] for r in accounts] or ["sandbox-001"]
    if mode == "Paper":
        account = controls[1].selectbox(
            "Account",
            choices,
            format_func=lambda value: value.replace("sandbox-", "Paper "),
            key="terminal-account",
        )
    else:
        controls[1].text_input("Account", "Not connected", disabled=True)
        account = st.session_state.get("terminal-account", choices[0])
    terminal(root, db, mode, account)

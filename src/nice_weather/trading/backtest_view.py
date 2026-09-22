"""Backtest presentation from persisted account facts; no strategy or execution rules."""

from __future__ import annotations

import json
import math
import time
from collections import Counter
from datetime import date, datetime

from fastapi import APIRouter, HTTPException

from nice_weather.trading.storage import connect, digest


def request_parameters(root, venue, payload):
    """Resolve date controls through task 1 and validate task 3 execution settings."""
    result = dict(payload)
    if "start_day" in result or "end_day" in result:
        try:
            from nice_weather.trading.market_weather import market_day
        except ModuleNotFoundError as exc:
            raise ValueError("Market-day API dependency is not installed") from exc
        start_day = date.fromisoformat(result["start_day"])
        end_day = date.fromisoformat(result["end_day"])
        if start_day > end_day:
            raise ValueError("End market day precedes start market day")
        first = market_day(root / "feed.sqlite3", venue, str(start_day))
        last = market_day(root / "feed.sqlite3", venue, str(end_day))
        def timestamp(value):
            if not value:
                raise ValueError("Contract observation window unavailable for selected market day")
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("Contract observation window lacks timezone")
            return parsed.timestamp()
        result.update(start=timestamp(first["observation_start"]),
                      end=min(timestamp(last["observation_end"]), time.time()),
                      window_basis="contract_observation_window")
    cash = result.get("cash", 100)
    if type(cash) not in (float, int) or not math.isfinite(cash) or not 0 < cash <= 1_000_000:
        raise ValueError("Invalid initial cash")
    if "simulation" in result:
        try:
            from nice_weather.trading.paper_execution import settings
        except ModuleNotFoundError as exc:
            raise ValueError("Paper execution settings dependency is not installed") from exc
        result["simulation"] = settings(result["simulation"])
    result["cash"] = cash
    return result


def account_point(snapshot):
    return {
        "time": snapshot["ts"] / 1e9,
        **{key: snapshot.get(key) for key in
           ("cash", "market_value", "equity", "total_pnl", "fees")},
        "settlement": "pending" if snapshot.get("positions") else
                      "settled" if snapshot.get("settled") else "no-position",
    }


def decision_events(decision):
    signal = decision["signal"]
    group = f"signal-{decision['feed_seq']}-{decision['native_index']}-{signal.get('strategy')}"
    # New producers supply explicit stages; legacy decisions retain their actual meaning.
    if signal.get("events") is not None:
        rows = []
        stages = {"weather_warning": "warning", "weather_trigger": "trigger",
                  "execution_rejected": "rejection", "waiting": "diagnostic"}
        for event in signal["events"]:
            # Fills come only from native account facts, never a signal's cumulative summary.
            if event["stage"] == "fill":
                continue
            legs = event.get("legs") or event.get("targets") or [{}]
            warning = event.get("warning") or {}
            if event["stage"] == "weather_warning" and warning.get("upper_bin"):
                legs = [{"token": warning["upper_bin"], "probability": warning.get("probability")}]
            for index, leg in enumerate(legs):
                if isinstance(leg, str):
                    leg = {"token": leg}
                rows.append({**event, **leg,
                             "id": f"{event.get('event_id', group)}-{index}",
                             "group_id": event.get("basket_id", group),
                             "time": event.get("asof", decision["received_at"]),
                             "stage": stages.get(event["stage"], event["stage"])})
        return rows
    stage = ("candidate" if signal.get("action") == "buy" else
             "trigger" if signal.get("triggered") else "diagnostic")
    return [{"id": f"{group}-{index}", "group_id": group,
             "time": decision["received_at"], "time_basis": "received_at",
             "day": signal.get("day"), "venue": signal.get("venue"),
             "strategy": signal.get("strategy"), "stage": stage,
             **{key: leg.get(key) for key in
                ("token", "price", "quantity", "cost", "probability")},
             "p_end": signal.get("p_end"),
             "reason": signal.get("execution_reason") or signal.get("reason")}
            for index, leg in enumerate(signal.get("legs") or [{}])]


def queued_runs(root):
    path = root / "requests" / "requests.sqlite3"
    if not path.exists():
        return []
    with connect(path, readonly=True) as con:
        return [{"run_id": row["request_id"], "account": row["account"],
                 "status": row["status"], "error": row["error"], "updated": row["created"],
                 "config": json.loads(row["payload"]) | {
                     "venue": row["account"].removeprefix("backtest-")}}
                for row in con.execute(
                    "SELECT * FROM requests WHERE mode='backtest' AND kind='backtest'")]


def run_detail(root, run_id):
    result, curve, events = None, [], []
    order_signals = {}
    if (root / "results.sqlite3").exists():
        with connect(root / "results.sqlite3", readonly=True) as con:
            row = con.execute("SELECT * FROM runs WHERE run_id=? AND mode='backtest'",
                              (run_id,)).fetchone()
            if row:
                result = dict(row)
                result["config"] = json.loads(result["config"])
                result["snapshot"] = json.loads(result["snapshot"] or "{}")
                for record in con.execute(
                        "SELECT body FROM inputs WHERE run_id=? ORDER BY seq", (run_id,)):
                    body = json.loads(record[0])
                    if body.get("kind") == "replay-view":
                        curve.extend(body["curve"])
                        events.extend(body["events"])
                    elif body.get("kind") == "replay-decisions":
                        for decision in body["decisions"]:
                            events.extend(decision_events(decision))
                            signal = decision["signal"]
                            legs = {leg["token"]: leg for leg in signal.get("legs", [])}
                            for execution in signal.get("executions", []):
                                if execution.get("order_id"):
                                    order_signals.setdefault(execution["order_id"], {
                                        "group_id": signal.get("basket_id"),
                                        "probability": legs.get(execution["token"], {}).get(
                                            "probability"), "p_end": signal.get("p_end")})
                if con.execute("SELECT 1 FROM sqlite_master WHERE name='simulation_equity'"
                               ).fetchone():
                    stored = [json.loads(r[0]) for r in con.execute(
                        "SELECT body FROM simulation_equity WHERE run_id=? ORDER BY ts", (run_id,))]
                    if stored:
                        curve = [{**r, "time": r["ts"] / 1e9} for r in stored]
    if result is None:
        result = next((r for r in queued_runs(root) if r["run_id"] == run_id), None)
        if result is None:
            raise HTTPException(404, "Backtest not found")
        result["snapshot"] = {}
    snapshot = result["snapshot"]
    complete = bool(curve)
    # Older runs expose only their actual final point, never an invented flat history.
    if not curve and snapshot.get("ts"):
        curve = [account_point(snapshot)]
    elif curve and snapshot.get("ts") and snapshot["ts"] / 1e9 >= curve[-1]["time"]:
        curve.append(account_point(snapshot))
    # Task 3/legacy snapshots may contain fills without replay-view records.
    observer = ReplayView()
    if snapshot.get("ts"):
        observer.observe(snapshot)
        known = {e["id"] for e in events}
        events.extend(e for e in observer.events if e["stage"] == "fill" and e["id"] not in known)
    events = list({e["id"]: e for e in events}.values())
    for event in events:
        for key, value in order_signals.get(event.get("order_id"), {}).items():
            event.setdefault(key, value)
    result.update(curve=sorted(curve, key=lambda p: p["time"]),
                  events=sorted(events, key=lambda e: e["time"]),
                  history_complete=complete,
                  settlement=account_point(snapshot)["settlement"] if snapshot.get("ts") else None)
    return result


class ReplayView:
    """Record minute observations plus order, fill and settlement changes."""

    def __init__(self):
        self.curve, self.events = [], []
        self.fills = Counter()
        self.orders = {}
        self.previous = None
        self.probe = None

    def observe_session(self, session):
        # No independent account arithmetic. The native snapshot owns all valuations.
        probe = (session.now // 60_000_000_000,
                 tuple((str(o.client_order_id), str(o.status), str(o.filled_qty))
                       for o in session.engine.cache.orders()), str(session.outcomes))
        if probe != self.probe:
            self.observe(session.snapshot())
            self.probe = probe

    def observe(self, snapshot, *, force=False):
        stamp = snapshot["ts"] / 1e9
        signature = (int(stamp // 60), snapshot.get("orders"), snapshot.get("settled"),
                     snapshot.get("equity") is None)
        if force or signature != self.previous:
            self.curve.append(account_point(snapshot))
            self.previous = signature
        markets = {m["token"]: m for m in snapshot.get("markets", [])}
        orders = {o["order_id"]: o for o in snapshot.get("orders", [])}
        for key, order in orders.items():
            signature = (order["status"], order.get("filled"))
            if self.orders.get(key) == signature:
                continue
            self.orders[key] = signature
            self.events.append({**order, "id": f"order-{key}-{signature}", "time": stamp,
                                "time_basis": "received_at", "venue": snapshot.get("venue"),
                                "stage": "rejection" if order["status"] in
                                {"REJECTED", "DENIED"} else "order",
                                "strategy": order.get("owner"),
                                "day": markets.get(order["token"], {}).get("date"),
                                "reason": order["status"]})
        occurrences = Counter()
        for fill in snapshot.get("fills", []):
            # Count occurrences: identical native fills at one timestamp remain separate.
            key = digest(fill)
            occurrences[key] += 1
            if occurrences[key] <= self.fills[key]:
                continue
            self.events.append({**fill, "id": f"fill-{key}-{occurrences[key]}",
                                "time": fill["ts"] / 1e9, "time_basis": "received_at",
                                "venue": snapshot.get("venue"), "stage": "fill",
                                "strategy": orders.get(fill["order_id"], {}).get("owner"),
                                "day": markets.get(fill["token"], {}).get("date"),
                                "cost": fill["price"] * fill["quantity"] + fill["fee"]
                                if fill.get("side") == "BUY" else None,
                                "proceeds": fill["price"] * fill["quantity"] - fill["fee"]
                                if fill.get("side") == "SELL" else None})
        self.fills |= occurrences

    def flush(self, results, run_id, cursor):
        with connect(results.path, readonly=True) as con:
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='simulation_equity'"
                           ).fetchone() and con.execute(
                               "SELECT 1 FROM simulation_equity WHERE run_id=? LIMIT 1",
                               (run_id,)).fetchone():
                self.curve = []  # Task 3 already persisted the account observations.
        if self.curve or self.events:
            results.append(run_id, f"replay-view-{cursor}",
                           {"kind": "replay-view", "curve": self.curve, "events": self.events})
            self.curve, self.events = [], []


def account_events(root, account, venue, day, after=0):
    """Project persisted signal stages and native fills for the selected market day."""
    path = root / "results.sqlite3"
    if not path.exists():
        return {"events": [], "next": after, "more": False}
    with connect(path, readonly=True) as con:
        run = con.execute("SELECT * FROM runs WHERE account=? AND mode='sandbox'",
                          (account,)).fetchone()
        if not run:
            return {"events": [], "next": after, "more": False}
        records = con.execute("SELECT seq,body FROM inputs WHERE run_id=? AND seq>? "
                              "ORDER BY seq LIMIT 1000", (run["run_id"], after)).fetchall()
        snapshot = json.loads(run["snapshot"] or "{}")
    events = []
    for record in records:
        body = json.loads(record["body"])
        if body.get("kind") == "signal-events":
            events.extend(decision_events({"feed_seq": record["seq"], "native_index": 0,
                                          "received_at": 0, "signal": body}))
    observer = ReplayView()
    if snapshot.get("ts"):
        observer.observe(snapshot)
        events.extend(e for e in observer.events if e["stage"] == "fill")
    events = [e for e in events if e.get("venue") == venue and e.get("day") == day]
    for event in events:
        if (event.get("token") or "").endswith(":NO"):
            event["outcome"] = "NO"
            event["token"] = event["token"].removesuffix(":NO")
    return {"events": events, "next": records[-1]["seq"] if records else after,
            "more": len(records) == 1000}


def router(root):
    routes = APIRouter(prefix="/api/backtests")

    @routes.get("")
    def listing():
        rows = {r["run_id"]: r for r in queued_runs(root)}
        if (root / "results.sqlite3").exists():
            with connect(root / "results.sqlite3", readonly=True) as con:
                for row in con.execute(
                        "SELECT run_id,account,status,error,updated,config FROM runs "
                        "WHERE mode='backtest' ORDER BY updated DESC"):
                    rows[row["run_id"]] = dict(row) | {"config": json.loads(row["config"])}
        return sorted(rows.values(), key=lambda r: r["updated"], reverse=True)

    @routes.get("/{run_id}")
    def detail(run_id: str):
        return run_detail(root, run_id)

    return routes

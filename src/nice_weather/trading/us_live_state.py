"""Live controls and projections in the existing results/transport databases."""

import json
import time
from decimal import Decimal

from nice_weather.trading.storage import connect, encoded


def defaults():
    return {
        "revision": 0,
        "binding": None,
        "manual_enabled": False,
        "strategies": {s: False for s in ("S1", "S2", "S3")},
        "whitelist": [],
        "order_limit": "5",
        "position_limit": "5",
        "daily_loss_limit": "5",
    }


def install(path):
    with connect(path) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS us_live_controls (
            account TEXT PRIMARY KEY, body TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS us_live_control_receipts (
            request_id TEXT PRIMARY KEY, account TEXT NOT NULL, body TEXT NOT NULL)""")


def controls(path, account):
    with connect(path) as con:
        row = con.execute(
            "SELECT body FROM us_live_controls WHERE account=?", (account,)
        ).fetchone()
    return json.loads(row[0]) if row else defaults()


def configure(path, account, request_id, payload, binding):
    """Commit configuration and its replay identity together, including stop intent."""
    allowed = set(defaults())
    if set(payload) - allowed or type(payload.get("revision")) is not int:
        raise ValueError("INVALID_CONFIG")
    with connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        prior = con.execute(
            "SELECT * FROM us_live_control_receipts WHERE request_id=?", (request_id,)
        ).fetchone()
        if prior:
            if prior["account"] != account or prior["body"] != encoded(payload):
                raise ValueError("REQUEST_ID_COLLISION")
            return
        row = con.execute(
            "SELECT body FROM us_live_controls WHERE account=?", (account,)
        ).fetchone()
        current = json.loads(row[0]) if row else defaults()
        if payload["revision"] != current["revision"]:
            raise ValueError("CONFIG_CHANGED")
        updated = current | payload
        if updated["binding"] not in (None, binding):
            raise ValueError("ACCOUNT_BINDING_MISMATCH")
        if type(updated["manual_enabled"]) is not bool:
            raise ValueError("INVALID_ENABLE_STATE")
        strategies = updated["strategies"]
        if (
            not isinstance(strategies, dict)
            or set(strategies) != {"S1", "S2", "S3"}
            or any(type(v) is not bool for v in strategies.values())
        ):
            raise ValueError("INVALID_STRATEGIES")
        markets = updated["whitelist"]
        if (
            not isinstance(markets, list)
            or any(not isinstance(m, str) or not m or len(m) > 256 for m in markets)
            or len(markets) != len(set(markets))
        ):
            raise ValueError("INVALID_WHITELIST")
        for field in ("order_limit", "position_limit", "daily_loss_limit"):
            value = Decimal(str(updated[field]))
            if not value.is_finite() or not 0 < value <= 5:
                raise ValueError("INVALID_RISK_LIMIT")
            updated[field] = str(value)
        updated["revision"] += 1
        con.execute(
            "INSERT OR REPLACE INTO us_live_controls VALUES (?,?)", (account, encoded(updated))
        )
        con.execute(
            "INSERT INTO us_live_control_receipts VALUES (?,?,?)",
            (request_id, account, encoded(payload)),
        )


def budget(path, venue):
    with connect(path) as con:
        rows = con.execute(
            "SELECT reserved,spent FROM live_test_budget WHERE venue=?", (venue,)
        ).fetchall()
    reserved = sum((Decimal(r["reserved"]) for r in rows), Decimal(0))
    spent = sum((Decimal(r["spent"]) for r in rows), Decimal(0))
    return {
        "limit": "5",
        "reserved": str(reserved),
        "spent": str(spent),
        "remaining": str(max(Decimal(0), 5 - reserved - spent)),
    }


def money_facts(venue, payload):
    """Display venue-defined amounts; never force buying power into cash balances."""

    def value(row, key):
        raw = row.get(key)
        if raw is None:
            return None
        number = Decimal(str(raw))
        if not number.is_finite():
            raise ValueError("INVALID_ACCOUNT_AMOUNT")
        return str(number)

    if venue == "poly_us":
        rows = payload["balances"]
        if len(rows) != 1 or rows[0]["currency"] != "USD":
            raise ValueError("USD_BALANCE_REQUIRED")
        row = rows[0]
        return {
            "cash": value(row, "currentBalance"),
            "available": value(row, "buyingPower"),
            "order_notional": value(row, "openOrders"),
            "reserved": value(row, "balanceReservation"),
            "position_value": value(row, "assetNotional"),
            "margin": value(row, "marginRequirement"),
            "cash_basis": "currentBalance：不含证券价值的现金",
            "available_basis": "buyingPower：含证券抵押价值及挂单影响",
            "reservation_basis": "balanceReservation；挂单名义金额另列",
        }
    available = value(payload, "balance_dollars")
    partitions = payload.get("balance_breakdown", [])
    indexes = [r["exchange_index"] for r in partitions]
    if len(indexes) != len(set(indexes)):
        raise ValueError("DUPLICATE_BALANCE_PARTITION")
    return {
        "cash": None,
        "available": available,
        "reserved": None,
        "position_value": str(Decimal(str(payload["portfolio_value"])) / 100),
        "partitions": {str(r["exchange_index"]): value(r, "balance") for r in partitions},
        "cash_basis": "平台接口提供可用余额，未单列含冻结资金的现金总额",
        "available_basis": "balance_dollars：全分区汇总，分区明细不重复相加",
        "reservation_basis": "平台未提供当前账户的独立冻结金额；项目预留另列",
    }


def availability(snapshot, action="order", owner="manual", now=None):
    now = time.time() if now is None else now
    config = snapshot.get("config", defaults())
    reason = None
    if action in {"configure", "stop"}:
        return {"allowed": True, "reason": None}
    if not snapshot.get("authenticated"):
        reason = "ACCOUNT_NOT_CONNECTED"
    elif now - snapshot.get("received_at", 0) > 10:
        reason = "ACCOUNT_STALE"
    elif action == "cancel":
        pass
    elif config["binding"] != snapshot.get("binding"):
        reason = "ACCOUNT_NOT_BOUND"
    elif not (
        config["manual_enabled"] if owner == "manual" else config["strategies"].get(owner, False)
    ):
        reason = "TRADING_NOT_ENABLED"
    elif not snapshot.get("reconciled"):
        reason = snapshot.get("reconcile_reason") or "ACCOUNT_RECONCILIATION_REQUIRED"
    return {"allowed": reason is None, "reason": reason}


def realized_today(reports, day):
    """Exact realized trading PnL including fees, by New York execution date."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    positions, result = {}, Decimal(0)
    for fill in sorted(reports, key=lambda f: (f.ts_event, f.trade_id.value)):
        net, average = positions.get(fill.instrument_id, (Decimal(0), Decimal(0)))
        qty, price = fill.last_qty.as_decimal(), fill.last_px.as_decimal()
        direction = Decimal(1) if fill.order_side.name == "BUY" else Decimal(-1)
        pnl = -fill.commission.as_decimal()
        if not net or net * direction > 0:
            average = (abs(net) * average + qty * price) / (abs(net) + qty)
        else:
            pnl += min(abs(net), qty) * (price - average) * (1 if net > 0 else -1)
            if qty > abs(net):
                average = price
        net += direction * qty
        positions[fill.instrument_id] = (net, average if net else Decimal(0))
        if datetime.fromtimestamp(fill.ts_event / 1e9, ZoneInfo("America/New_York")).date() == day:
            result += pnl
    return result

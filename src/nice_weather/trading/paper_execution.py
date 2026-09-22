"""Quote approximation shared by Paper, replay and the read-only ticket preview."""

from __future__ import annotations

import json
import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

VERSION = "market-price-v1"
DEFAULTS = {"estimated_fee_rate": 0.01, "slippage_pp": 0}


def ensure_equity(con):
    con.execute("""CREATE TABLE IF NOT EXISTS simulation_equity (
        run_id TEXT NOT NULL, ts INTEGER NOT NULL, body TEXT NOT NULL,
        PRIMARY KEY(run_id,ts))""")


def save_equity(con, run_id, snapshot):
    from nice_weather.trading.storage import encoded

    fields = (
        "ts",
        "cash",
        "reserved",
        "available",
        "market_value",
        "equity",
        "fees",
        "realized_pnl",
        "unrealized_pnl",
        "total_pnl",
        "settlement_status",
    )
    con.execute(
        "INSERT OR REPLACE INTO simulation_equity VALUES (?,?,?)",
        (run_id, snapshot["ts"], encoded({k: snapshot.get(k) for k in fields})),
    )


def settings(values=None):
    result = DEFAULTS | (values or {})
    if set(result) != set(DEFAULTS):
        raise ValueError("Unknown simulation setting")
    for key, maximum in (("estimated_fee_rate", 1), ("slippage_pp", 100)):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Invalid {key}")
        if not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(f"Invalid {key}")
    return result


def decimal(value, name):
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"Invalid {name}") from exc
    if isinstance(value, bool) or not result.is_finite() or result <= 0:
        raise ValueError(f"Invalid {name}")
    return result


def price(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (float, int))
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def ingest(previous, row, definition, now):
    """Retain bounded latest evidence, never stamp an old price with a new receipt."""
    result = dict(previous or {})
    for key, expected in (
        ("venue", definition["venue"]),
        ("market_day", definition["local_day"]),
        (
            "token_id",
            definition["yes_token_id"]
            if definition["outcome"] == "YES"
            else definition["no_token_id"],
        ),
    ):
        if key in row and row[key] != expected:
            raise ValueError(f"Market price {key} mismatch")
    received = row.get("received_at")
    basis = "received_at" if received is not None else row.get("time_basis")
    known = received if received is not None else row.get("source_time")
    if basis not in {"received_at", "source_time"} or not isinstance(known, (int, float)):
        raise ValueError("Market price requires a known time basis")
    if not math.isfinite(known) or known > now:
        raise ValueError("Future market price")
    if known < result.get("known_at", -1):
        return result
    evidence = {
        "source_time": row.get("source_time"),
        "received_at": received,
        "known_at": known,
        "time_basis": basis,
        "venue": definition["venue"],
        "market_day": definition["local_day"],
        "source": row.get("source", "market_feed"),
    }
    if row.get("valid", True):
        bid, ask = row.get("best_bid"), row.get("best_ask")
        if price(bid) and price(ask) and bid > ask:
            raise ValueError("Crossed market price")
        current = {}
        for key in ("best_bid", "best_ask", "mid", "last_trade"):
            value = row.get(key)
            if price(value):
                current[key] = evidence | {"price": value}
        if price(bid) and price(ask):
            current["mid"] = evidence | {"price": (bid + ask) / 2}
        if current:
            result.update(current=current, known_at=known)
            if "last_trade" in current:
                result["last_trade"] = current["last_trade"]
            for side, preferred in (("BUY", "best_ask"), ("SELL", "best_bid")):
                candidate = (
                    current.get(preferred)
                    or current.get("mid")
                    or current.get("last_trade")
                    or current.get("best_bid")
                    or current.get("best_ask")
                )
                result[side] = candidate
    else:
        result["current"] = {}
    return result


def select(prices, side, now, options=None, tick=None):
    current = prices.get("current", {})
    chosen, kind = None, None
    for key, item in (
        (
            "ask" if side == "BUY" else "bid",
            current.get("best_ask" if side == "BUY" else "best_bid"),
        ),
        ("mid", current.get("mid")),
        ("last_trade", prices.get("last_trade")),
        ("last_quote", prices.get(side)),
    ):
        if item and item["known_at"] <= now:
            chosen, kind = item, key
            break
    if chosen is None:
        raise ValueError("NO_MARKET_PRICE: no market price known at execution time")
    value = Decimal(str(chosen["price"]))
    slip = Decimal(str(settings(options)["slippage_pp"])) / 100
    value += slip if side == "BUY" else -slip
    if not 0 <= value <= 1:
        raise ValueError("Slippage exceeds binary price range")
    if tick:
        step = Decimal(str(tick))
        value = (value / step).to_integral_value(
            rounding=ROUND_CEILING if side == "BUY" else ROUND_FLOOR
        ) * step
    return chosen | {
        "price": float(value),
        "raw_price": chosen["price"],
        "price_source": kind,
        "age_seconds": now - chosen["known_at"],
        "approximate": True,
        "execution_model": VERSION,
    }


def fee(quantity, value, definition, options, side, accumulator=None):
    q, p = Decimal(str(quantity)), Decimal(str(value))
    if not definition.get("fee_known"):
        return (q * p * Decimal(str(settings(options)["estimated_fee_rate"]))).quantize(
            Decimal("0.01"), rounding=ROUND_CEILING
        )
    if definition.get("fee_rounding") in {"poly_us_order_half_even_v1", "kalshi_order_balance_v1"}:
        from nice_weather.trading.us_fees import charge

        return charge(q, p, definition, accumulator if accumulator is not None else {}, side=side)
    from nice_weather.trading.signals import fee as venue_fee

    return Decimal(str(venue_fee(q, p, definition)))


def preview(config, metadata, prices, snapshot, payload, now, owner="manual"):
    """No native imports: the API and engine validate against the same account projection."""
    from nice_weather.trading.dataset import timestamp

    options = settings(config.get("simulation"))
    result = {
        "available": False,
        "reason": None,
        "execution_model": VERSION,
        "approximate": True,
        "simulation": options,
    }
    try:
        token, side = payload["token"], payload["side"]
        row = metadata[token]
        if (
            row["venue"] != config["venue"]
            or row["station_id"] != "KNYC"
            or row["timezone"] != "America/New_York"
            or row["parse_status"] != "parsed"
            or json.loads(row["ambiguities_json"])
        ):
            raise ValueError("Ambiguous contract: no-trade")
        if row.get("received_at", now) > now:
            raise ValueError("Future contract")
        if (
            row["closed"]
            or not row["active"]
            or not row["accepting_orders"]
            or now * 1e9
            >= min(
                timestamp(row["observation_end"]),
                timestamp(row.get("close_time", row["observation_end"])),
            )
            or token in snapshot.get("settled", {})
        ):
            raise ValueError("Market closed")
        if side not in {"BUY", "SELL"} or payload.get("tif", "GTC") not in {"GTC", "IOC", "FOK"}:
            raise ValueError("Unsupported side or time in force")
        limit, quantity = (
            decimal(payload["price"], "price"),
            decimal(payload["quantity"], "quantity"),
        )
        if limit >= 1 or limit % Decimal(str(row["tick_size"])):
            raise ValueError("Illegal price / tick precision")
        if quantity < Decimal(str(row["minimum_order_size"])) or quantity % Decimal(
            str(row.get("quantity_step", "0.000001"))
        ):
            raise ValueError("Illegal quantity precision or below market minimum")
        if payload.get("post_only"):
            raise ValueError("Approximate simulation does not model maker priority")
        selected = select(prices.get(token, {}), side, now, options, row["tick_size"])
        crosses = (
            selected["price"] <= float(limit)
            if side == "BUY"
            else selected["price"] >= float(limit)
        )
        estimate = fee(quantity, selected["price"], row, options, side)
        # Resting buys reserve their limit and the largest fee within that interval.
        reserve_fee = max(
            fee(quantity, p, row, options, side) for p in (limit, min(limit, Decimal("0.5")))
        )
        open_orders = [
            o
            for o in snapshot.get("orders", [])
            if o["remaining"] > 0
            and o["status"] not in {"CANCELED", "REJECTED", "DENIED", "EXPIRED", "FILLED"}
        ]
        if side == "SELL":
            held = sum(p["quantity"] for p in snapshot.get("positions", []) if p["token"] == token)
            reserved = sum(
                o["remaining"] for o in open_orders if o["token"] == token and o["side"] == side
            )
            if float(quantity) > held - reserved + 1e-9:
                raise ValueError("Naked sell / shares already reserved")
            if crosses and float(estimate - Decimal(str(selected["price"])) * quantity) > (
                snapshot["available"] + 1e-9
            ):
                raise ValueError("Insufficient cash for sell fees")
        else:
            required = (
                Decimal(str(selected["price"])) * quantity + estimate
                if crosses
                else limit * quantity + reserve_fee
            )
            if float(required) > snapshot["available"] + 1e-9:
                raise ValueError("Insufficient cash including fees and open orders")
            if owner != "manual":
                exposure = sum(
                    p["cost"]
                    for p in snapshot.get("positions", [])
                    if p["date"] == row["local_day"]
                )
                bin_exposure = sum(
                    p["cost"]
                    for p in snapshot.get("positions", [])
                    if p["condition"] == row["condition_id"]
                )
                for order in open_orders:
                    other = metadata[order["token"]]
                    if order["side"] == "BUY" and other["local_day"] == row["local_day"]:
                        exposure += order["remaining"] * order["price"]
                        if other["condition_id"] == row["condition_id"]:
                            bin_exposure += order["remaining"] * order["price"]
                if exposure + float(limit * quantity) > config.get(
                    "max_day_notional", 20
                ) or bin_exposure + float(limit * quantity) > config.get("max_bin_notional", 5):
                    raise ValueError("Strategy exposure limit")
        result.update(
            available=True,
            selected_price=selected,
            estimated_fee=float(estimate),
            fee_estimated=not row.get("fee_known"),
            crosses=crosses,
            expected_status="FILLED"
            if crosses
            else ("ACCEPTED" if payload.get("tif", "GTC") == "GTC" else "CANCELED"),
        )
    except (KeyError, ValueError, ArithmeticError) as exc:
        result["reason"] = str(exc)
    return result

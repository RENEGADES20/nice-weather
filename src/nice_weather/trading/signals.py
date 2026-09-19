"""Frozen S1/S2/S3 selection shared by replay and online execution.

Probabilities are a station/settlement-specific model input, never inferred from prices.
The caller journals the first trigger before execution, including rejected triggers.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import ROUND_CEILING, Decimal
from zoneinfo import ZoneInfo

VERSION = "knyc-executable-v1"
STRATEGY_IDS = ("S1", "S2", "S3")


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def fee(quantity, price, contract):
    """Use the explicitly versioned per-contract schedule; unknown fees fail closed."""
    if not contract.get("fee_known"):
        raise ValueError("UNKNOWN_FEES")
    rate = Decimal(str(contract["fee_rate"]))
    exponent = Decimal(str(contract.get("fee_exponent", 1)))
    p, q = Decimal(str(price)), Decimal(str(quantity))
    if not p.is_finite() or not q.is_finite() or not 0 <= p <= 1 or q < 0:
        raise ValueError("INVALID_FEE_INPUT")
    if not rate.is_finite() or rate < 0 or not exponent.is_finite() or exponent < 0:
        raise ValueError("INVALID_FEES")
    amount = q * rate * (p * (1 - p)) ** exponent
    if contract.get("fee_rounding") == "ceil_cent":
        amount = amount.quantize(Decimal(".01"), rounding=ROUND_CEILING)
    elif contract.get("fee_rounding") != "exact":
        raise ValueError("UNKNOWN_FEE_ROUNDING")
    return float(amount)


def select(strategy, contracts, weather, asof):
    """Return target indexes or None; invalid inputs cannot consume a weather trigger."""
    if strategy not in STRATEGY_IDS:
        raise ValueError("UNKNOWN_STRATEGY")
    required = ("received_at", "data_cutoff", "p_end", "floor", "model_trained_at")
    if any(not finite(weather.get(k)) for k in required):
        raise ValueError("INVALID_WEATHER_INPUT")
    if not isinstance(weather.get("model_version"), str) or not weather["model_version"].startswith(
        "knyc-"
    ):
        raise ValueError("KNYC_MODEL_REQUIRED")
    if weather.get("station") != "KNYC" or not contracts:
        raise ValueError("STATION_OR_CONTRACT_MISSING")
    if not weather["model_trained_at"] <= weather["data_cutoff"] <= weather["received_at"] <= asof:
        raise ValueError("FUTURE_INPUT")
    if asof - weather["received_at"] > 120 or not 0 <= weather["p_end"] <= 1:
        raise ValueError("STALE_OR_INVALID_WEATHER")
    local = datetime.fromtimestamp(asof, ZoneInfo("America/New_York"))
    if not 12 <= local.hour + local.minute / 60 <= 22:
        return None
    if weather.get("day") != contracts[0]["local_day"]:
        raise ValueError("WEATHER_DAY_MISMATCH")
    if weather["day"] != local.date().isoformat():
        raise ValueError("SIGNAL_DAY_EXPIRED")
    if len({c["yes_token_id"] for c in contracts}) != len(contracts):
        raise ValueError("DUPLICATE_BIN")
    for c in contracts:
        for boundary in (c["lower"], c["upper"]):
            if boundary is not None and (not finite(boundary) or boundary != int(boundary)):
                raise ValueError("INVALID_BIN_BOUNDARY")
        if c["lower"] is not None and c["upper"] is not None and c["lower"] > c["upper"]:
            raise ValueError("INVALID_BIN_BOUNDARY")
    if any(
        c["station_id"] != "KNYC"
        or c["local_day"] != weather["day"]
        or c["venue"] != contracts[0]["venue"]
        or c.get("settlement_source") != weather.get("settlement_source")
        or c["received_at"] > asof
        or c.get("parse_status") != "parsed"
        for c in contracts
    ):
        raise ValueError("CONTRACT_MODEL_MISMATCH")
    probs = weather.get("probabilities", {})
    if not isinstance(probs, dict):
        raise ValueError("INVALID_BIN_PROBABILITIES")
    q = [probs.get(c["yes_token_id"]) for c in contracts]
    if any(not finite(p) or not 0 <= p <= 1 for p in q) or abs(sum(q) - 1) > 1e-6:
        raise ValueError("INVALID_BIN_PROBABILITIES")
    # Integer Fahrenheit bins must form one complete non-overlapping partition.
    if contracts[0]["lower"] is not None or contracts[-1]["upper"] is not None:
        raise ValueError("INCOMPLETE_BINS")
    for a, b in zip(contracts, contracts[1:], strict=False):
        if a["upper"] is None or b["lower"] is None or a["upper"] + 1 != b["lower"]:
            raise ValueError("NONADJACENT_BINS")
    if strategy == "S2":
        previous = weather.get("previous_floor")
        if not finite(previous) or weather["floor"] <= previous:
            return None
        if not weather.get("is_high") or not weather.get("observation_received_at"):
            return None
        if (
            not finite(weather["observation_received_at"])
            or not 0 <= weather["data_cutoff"] - weather["observation_received_at"] <= 120
        ):
            raise ValueError("FUTURE_HIGH_EVENT")

        def containing(value):
            return next(
                (
                    i
                    for i, c in enumerate(contracts)
                    if (c["lower"] is None or c["lower"] <= value)
                    and (c["upper"] is None or value <= c["upper"])
                ),
                None,
            )

        index = containing(weather["floor"])
        if index is None or index == containing(previous) or q[index] < 0.9:
            return None
        return [index]
    if weather["p_end"] < 0.9:
        return None
    if strategy == "S1":
        if len(q) < 2:
            raise ValueError("MISSING_ADJACENT_BIN")
        index = max(range(len(q) - 1), key=lambda i: q[i] + q[i + 1])
        return [index, index + 1]
    return [max(range(len(q)), key=q.__getitem__)]


def evaluate(strategy, contracts, weather, books, asof):
    output = {
        "strategy": strategy,
        "version": VERSION,
        "station": "KNYC",
        "venue": contracts[0]["venue"] if contracts else None,
        "day": weather.get("day"),
        "asof": asof,
        "model_version": weather.get("model_version"),
        "data_cutoff": weather.get("data_cutoff"),
        "action": "no-trade",
        "triggered": False,
        "legs": [],
    }
    try:
        indexes = select(strategy, contracts, weather, asof)
        if indexes is None:
            return output | {"reason": "WAITING_FOR_WEATHER_TRIGGER"}
        output["triggered"] = True
        probability = cost = 0.0
        for index in indexes:
            c = contracts[index]
            token = c["yes_token_id"]
            book = books.get(token)
            if not book or not book.get("complete") or not 0 <= asof - book["received_at"] <= 30:
                raise ValueError("MISSING_OR_STALE_BOOK")
            if not book.get("bids") or not book.get("asks"):
                raise ValueError("ONE_SIDED_BOOK")
            if max(p for p, _ in book["bids"]) >= min(p for p, _ in book["asks"]):
                raise ValueError("CROSSED_BOOK")
            if not c.get("active") or c.get("closed") or not c.get("accepting_orders"):
                raise ValueError("MARKET_CLOSED")
            left, principal, limit = 1.0, 0.0, None
            for price, size in sorted(book["asks"]):
                if not finite(price) or not 0 < price < 1 or not finite(size) or size < 0:
                    raise ValueError("INVALID_DEPTH")
                take = min(left, size)
                principal += take * price
                left -= take
                if take:
                    limit = price
                if left <= 1e-9:
                    break
            if left > 1e-9 or limit is None:
                raise ValueError("INSUFFICIENT_DEPTH")
            # Reserve at limit, not optimistic average; includes conservative per-order rounding.
            executable = limit + fee(1, limit, c)
            probability += weather["probabilities"][token]
            cost += executable
            output["legs"].append(
                {
                    "token": token,
                    "quantity": 1,
                    "side": "BUY",
                    "tif": "IOC",
                    "price": limit,
                    "cost": executable,
                    "depth_cost": principal,
                }
            )
        output.update(probability=probability, cost=cost, net_edge=probability - cost)
        if probability - cost <= 1e-12:
            raise ValueError("NO_NET_EDGE")
        return output | {"action": "buy", "reason": "POSITIVE_NET_EDGE"}
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        # No legs may escape a rejected basket, including failure on its second leg.
        return output | {"reason": str(exc), "legs": []}

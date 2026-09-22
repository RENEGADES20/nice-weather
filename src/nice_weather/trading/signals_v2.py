"""Continuous strategy decisions; frozen research selection stays in signals.py."""

import json
from bisect import bisect_right
from decimal import ROUND_CEILING, Decimal

from nice_weather.trading.signals import containing, evidence_time, finite, select, validate_weather
from nice_weather.trading.storage import digest
from nice_weather.trading.us_fees import charge as fee

VERSION = "knyc-executable-v2"


def opportunity_status(state, executions):
    """Task 3 supplies cumulative native order feedback; duplicates cannot restore a used day."""
    if "attempt" not in state or state.get("consumed") or any(
        finite(e.get("filled")) and e["filled"] > 0 for e in executions
    ):
        state["consumed"] = True
        return "DAILY_OPPORTUNITY_CONSUMED"
    if any(not finite(e.get("filled")) or e["filled"] < 0 or
           e.get("status") not in {"CANCELED", "REJECTED", "EXPIRED", "DENIED"}
           for e in executions):
        return "ORDER_RECONCILIATION_REQUIRED"
    return None


def decimal(value):
    if isinstance(value, bool):
        raise ValueError("INVALID_AMOUNT")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("INVALID_AMOUNT")
    return result


def candidates(contract, book, probability, budget, asof, *, single=False, context="online"):
    """Quantity grid with conservative IOC limit cost, sorted by total cost."""
    if not book:
        raise ValueError("MISSING_OR_STALE_BOOK")
    stamp = evidence_time(book, context)
    if stamp > asof:
        raise ValueError("FUTURE_QUOTE")
    if context == "online":
        if not book.get("complete") or asof - stamp > 30:
            raise ValueError("MISSING_OR_STALE_BOOK")
    elif not finite(book.get("valid_until")) or not stamp <= asof < book["valid_until"]:
        raise ValueError("HISTORICAL_QUOTE_GAP")
    # Task 3 may supply an explicitly priced approximation; capacity is not invented here.
    approximate = context != "online" and "price" in book
    if approximate:
        if not book.get("price_source"):
            raise ValueError("MISSING_MARKET_PRICE_SOURCE")
        price = book["price"]
        capacity = book.get("max_quantity")
        if not finite(capacity) or capacity <= 0:
            raise ValueError("MISSING_QUANTITY_BOUND")
        asks, bids = [(price, capacity)], []
    else:
        bids, asks = book.get("bids", []), sorted(book.get("asks", []))
    if not asks or (not approximate and not bids):
        raise ValueError("ONE_SIDED_BOOK")
    for price, size in bids + asks:
        if not finite(price) or not 0 < price < 1 or not finite(size) or size < 0:
            raise ValueError("INVALID_DEPTH")
    asks = [(p, q) for p, q in asks if q > 0]
    if not asks:
        raise ValueError("INSUFFICIENT_DEPTH")
    if bids and max(p for p, _ in bids) >= asks[0][0]:
        raise ValueError("CROSSED_BOOK")
    if not contract.get("active") or contract.get("closed") or not contract.get("accepting_orders"):
        raise ValueError("MARKET_CLOSED")
    step = decimal(contract["quantity_step"])
    tick = decimal(contract["tick_size"])
    minimum = decimal(contract["minimum_order_size"])
    if not step or not minimum or not tick:
        raise ValueError("INVALID_MARKET_PRECISION")
    if any(decimal(p) % tick for p, _ in asks):
        raise ValueError("INVALID_PRICE_TICK")
    low = int((max(Decimal(1), minimum) / step).to_integral_value(rounding=ROUND_CEILING))
    high = int(min(sum(decimal(q) for _, q in asks), budget / decimal(asks[0][0])) / step)
    if single:
        if minimum > 1 or Decimal(1) % step:
            raise ValueError("ONE_SHARE_BELOW_MARKET_MINIMUM")
        low = high = int(Decimal(1) / step)
    # ponytail: bounded exact grid; use piecewise fee/depth breakpoints above 100k quantities.
    if high - low > 100_000:
        raise ValueError("QUANTITY_GRID_LIMIT")
    options = []
    level, available, principal = 0, Decimal(0), Decimal(0)
    for units in range(low, high + 1):
        quantity = units * step
        while level < len(asks) and available + decimal(asks[level][1]) < quantity:
            p, q = map(decimal, asks[level])
            available += q
            principal += p * q
            level += 1
        if level == len(asks):
            break
        price = decimal(asks[level][0])
        reserve = quantity * price + decimal(fee(quantity, price, contract))
        if reserve > budget:
            break
        options.append(
            {
                "token": contract["yes_token_id"],
                "quantity": quantity,
                "price": price,
                "cost": reserve,
                "depth_cost": principal + (quantity - available) * price,
                "edge": decimal(probability) * quantity - reserve,
            }
        )
    return options


def optimize(options, budget):
    """Exact two-leg objective via the best affordable prefix, O(n log n)."""
    if any(not rows for rows in options):
        raise ValueError("NO_FEASIBLE_QUANTITY")
    if len(options) == 1:
        return [options[0][0]]
    second = options[1]
    costs, best_prefix = [], []
    best = None
    for row in second:
        costs.append(row["cost"])
        rank = (row["edge"], -row["cost"], -row["quantity"])
        if best is None or rank > best[0]:
            best = rank, row
        best_prefix.append(best[1])
    choice = None
    for left in options[0]:
        index = bisect_right(costs, budget - left["cost"]) - 1
        if index < 0:
            continue
        right = best_prefix[index]
        rank = (
            left["edge"] + right["edge"],
            -left["cost"] - right["cost"],
            -left["quantity"],
            -right["quantity"],
        )
        if choice is None or rank > choice[0]:
            choice = rank, [left, right]
    if choice is None:
        raise ValueError("NO_FEASIBLE_QUANTITY")
    return choice[1]


def _evaluate(strategy, contracts, weather, books, asof, risk, context):
    output = {
        "strategy": strategy,
        "version": VERSION,
        "station": "KNYC",
        "venue": contracts[0]["venue"] if contracts else None,
        "day": weather.get("day"),
        "asof": asof,
        "data_cutoff": weather.get("data_cutoff"),
        "model_version": weather.get("model_version"),
        "model_data_cutoff": weather.get("model_data_cutoff"),
        "model_generated_at": weather.get("model_trained_at"),
        "p_end": weather.get("p_end"),
        "probabilities": weather.get("probabilities", {}),
        "model_validation": weather.get("validation"),
        "time_basis": context,
        "received_at": weather.get("received_at"),
        "source_time": weather.get("source_time"),
        "capture_ids": weather.get("capture_ids", []),
        "rules_versions": [c.get("rules_version") for c in contracts],
        "action": "no-trade",
        "triggered": False,
        "legs": [],
    }
    try:
        if weather.get("status") == "unavailable":
            raise ValueError(weather.get("reason", "WEATHER_INPUT_UNAVAILABLE"))
        in_window = validate_weather(strategy, weather, asof, context)
        output["input_available"] = True
        output["in_window"] = in_window
        output["triggered"] = in_window and strategy in {"S1", "S3"} and weather["p_end"] >= .9
        if not in_window:
            return output | {"reason": "OUTSIDE_STRATEGY_WINDOW"}
        if not contracts:
            raise ValueError("CONTRACT_HISTORY_MISSING")
        if any(c.get("parse_status") != "parsed" for c in contracts):
            raise ValueError("CONTRACT_RULES_UNVERIFIED")
        indexes = select(strategy, contracts, weather, asof, context=context)
        if strategy == "S2":
            floor = weather["floor"]
            for i, c in enumerate(contracts[:-1]):
                if (c["lower"] is None or floor >= c["lower"]) and floor <= c["upper"]:
                    distance = contracts[i + 1]["lower"] - floor
                    output["warning"] = {
                        "distance": distance,
                        "upper_bin": contracts[i + 1]["yes_token_id"],
                        "probability": weather["probabilities"][contracts[i + 1]["yes_token_id"]],
                        "near_boundary": 0 < distance <= 1,
                    }
                    break
        if indexes is None:
            reason = "END_PROBABILITY_BELOW_90"
            if strategy == "S2":
                previous = weather.get("previous_floor")
                crossed = finite(previous) and weather.get("is_high") and (
                    weather["floor"] > previous and
                    containing(contracts, weather["floor"]) != containing(contracts, previous)
                )
                reason = "NEW_BIN_PROBABILITY_BELOW_90" if crossed else "NO_ACTUAL_HIGH_CROSSING"
            return output | {"reason": reason}
        output["triggered"] = True
        output["targets"] = [
            {"token": contracts[i]["yes_token_id"],
             "probability": weather["probabilities"][contracts[i]["yes_token_id"]]}
            for i in indexes
        ]
        if not risk:
            raise ValueError("MISSING_CANDIDATE_BUDGET")
        budget = min(
            decimal(risk[k]) for k in ("cash", "budget", "day_remaining", "loss_remaining")
        )
        options = []
        for index in indexes:
            c = contracts[index]
            token = c["yes_token_id"]
            cap = min(budget, decimal(risk["bin_remaining"][token]))
            options.append(
                candidates(
                    c,
                    books.get(token),
                    weather["probabilities"][token],
                    cap,
                    asof,
                    single=strategy != "S1",
                    context=context,
                )
            )
        legs = optimize(options, budget)
        edge = sum(row["edge"] for row in legs)
        if edge <= 0:
            raise ValueError("NO_NET_EDGE")
        cost = sum(row["cost"] for row in legs)
        output.update(
            probability=sum(
                weather["probabilities"][contracts[i]["yes_token_id"]] for i in indexes
            ),
            cost=float(cost),
            net_edge=float(edge),
            rules_versions=[contracts[i].get("rules_version") for i in indexes],
            probabilities={
                c["yes_token_id"]: weather["probabilities"][c["yes_token_id"]] for c in contracts
            },
            settlement_pnl={
                c["yes_token_id"]: float(
                    sum(row["quantity"] for row in legs if row["token"] == c["yes_token_id"]) - cost
                )
                for c in contracts
            },
            legs=[
                {
                    k: float(v) if isinstance(v, Decimal) else v
                    for k, v in row.items()
                    if k != "edge"
                }
                | {"side": "BUY", "tif": "IOC",
                   "probability": weather["probabilities"][row["token"]],
                   "quote_received_at": books[row["token"]].get("received_at"),
                   "quote_source_time": books[row["token"]].get("source_time"),
                   "quote_age_seconds": asof - evidence_time(books[row["token"]], context),
                   "price_source": books[row["token"]].get("price_source", "ask_depth")}
                for row in legs
            ],
        )
        return output | {"action": "buy", "reason": "POSITIVE_NET_EDGE"}
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        return output | {"reason": str(exc), "legs": []}


def evaluate(strategy, contracts, weather, books, asof, risk, *, context="online"):
    signal = _evaluate(strategy, contracts, weather, books, asof, risk, context)
    # Invalid probabilities still produce a serializable rejection for the journal/UI.
    signal = json.loads(json.dumps(signal), parse_constant=lambda _: None)
    signal["signal_id"] = digest(signal)
    signal["basket_id"] = signal["signal_id"]
    signal["stage"] = "candidate" if signal["action"] == "buy" else (
        "weather_trigger" if signal["triggered"] else (
            "weather_warning" if signal.get("warning", {}).get("near_boundary")
            and signal["reason"] != "OUTSIDE_STRATEGY_WINDOW" else "waiting"
        )
    )
    signal["events"] = signal_events(signal)
    return signal


def signal_events(signal):
    """Small chart/diagnostic events, with no duplicated input payloads or fake fills."""
    stages = []
    if signal.get("warning", {}).get("near_boundary") and (
        signal.get("reason") != "OUTSIDE_STRATEGY_WINDOW"
    ):
        stages.append(("weather_warning", "NEAR_UPPER_BIN"))
    if signal.get("triggered"):
        stages.append(("weather_trigger", "WEATHER_CONDITION_MET"))
    if signal.get("action") == "buy":
        stages.append(("candidate", "POSITIVE_NET_EDGE"))
    if signal.get("execution_reason"):
        stages.append(("execution_rejected", signal["execution_reason"]))
    if signal.get("executions"):
        stages.append(("order", "NATIVE_ORDER_STATUS"))
    if any(e.get("filled", 0) > 0 for e in signal.get("executions", [])):
        stages.append(("fill", "NATIVE_FILL"))
    if not stages:
        stages.append(("waiting", signal["reason"]))
    rows = []
    for stage, reason in stages:
        row = {k: signal.get(k) for k in (
            "signal_id", "basket_id", "account", "mode", "venue", "day", "strategy",
            "asof", "time_basis", "received_at", "source_time", "version", "model_version",
            "model_validation", "rules_versions", "p_end", "capture_ids",
            "execution_eligible", "model_data_cutoff", "model_generated_at",
        )}
        row.update(stage=stage, reason=reason, candidate_reason=signal["reason"],
                   legs=signal.get("legs", []), targets=signal.get("targets", []),
                   warning=signal.get("warning"), executions=signal.get("executions", []))
        row["event_id"] = digest(row)
        rows.append(row)
    return rows

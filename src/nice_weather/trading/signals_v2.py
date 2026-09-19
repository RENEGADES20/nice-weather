"""Continuous strategy decisions; frozen research selection stays in signals.py."""

from bisect import bisect_right
from decimal import ROUND_CEILING, Decimal

from nice_weather.trading.signals import finite, select
from nice_weather.trading.us_fees import charge as fee

VERSION = "knyc-executable-v2"


def decimal(value):
    if isinstance(value, bool):
        raise ValueError("INVALID_AMOUNT")
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("INVALID_AMOUNT")
    return result


def candidates(contract, book, probability, budget, asof, *, single=False):
    """Quantity grid with conservative IOC limit cost, sorted by total cost."""
    if not book or not book.get("complete") or not 0 <= asof - book["received_at"] <= 30:
        raise ValueError("MISSING_OR_STALE_BOOK")
    bids, asks = book.get("bids", []), sorted(book.get("asks", []))
    if not bids or not asks:
        raise ValueError("ONE_SIDED_BOOK")
    for price, size in bids + asks:
        if not finite(price) or not 0 < price < 1 or not finite(size) or size < 0:
            raise ValueError("INVALID_DEPTH")
    asks = [(p, q) for p, q in asks if q > 0]
    if not asks:
        raise ValueError("INSUFFICIENT_DEPTH")
    if max(p for p, _ in bids) >= asks[0][0]:
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


def evaluate(strategy, contracts, weather, books, asof, risk):
    output = {
        "strategy": strategy,
        "version": VERSION,
        "station": "KNYC",
        "venue": contracts[0]["venue"] if contracts else None,
        "day": weather.get("day"),
        "asof": asof,
        "data_cutoff": weather.get("data_cutoff"),
        "model_version": weather.get("model_version"),
        "p_end": weather.get("p_end"),
        "action": "no-trade",
        "triggered": False,
        "legs": [],
    }
    try:
        if weather.get("status") == "unavailable":
            raise ValueError(weather.get("reason", "WEATHER_INPUT_UNAVAILABLE"))
        if any(c.get("parse_status") != "parsed" for c in contracts):
            raise ValueError("CONTRACT_RULES_UNVERIFIED")
        indexes = select(strategy, contracts, weather, asof)
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
            return output | {"reason": "WAITING_FOR_WEATHER_TRIGGER"}
        output["triggered"] = True
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
                | {"side": "BUY", "tif": "IOC"}
                for row in legs
            ],
        )
        return output | {"action": "buy", "reason": "POSITIVE_NET_EDGE"}
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        return output | {"reason": str(exc), "legs": []}

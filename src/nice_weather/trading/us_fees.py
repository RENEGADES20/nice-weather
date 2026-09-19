"""Versioned US taker charges. Order accumulators contain decimal strings for recovery."""

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal

from nice_weather.trading.signals import fee as frozen_fee


def model(quantity, price, contract):
    q, p = Decimal(str(quantity)), Decimal(str(price))
    rate = Decimal(str(contract["fee_rate"]))
    exponent = Decimal(str(contract.get("fee_exponent", 1)))
    if (not contract.get("fee_known") or any(not x.is_finite() for x in (q, p, rate, exponent))
            or q < 0 or not 0 <= p <= 1 or rate < 0 or exponent < 0):
        raise ValueError("INVALID_OR_UNKNOWN_FEES")
    return q * rate * (p * (1 - p)) ** exponent


def charge(quantity, price, contract, state=None, *, side="BUY"):
    """One actual taker fill; pass the same state for every fill of an order."""
    state = {} if state is None else state
    exact = model(quantity, price, contract)
    rounding = contract.get("fee_rounding")
    if rounding == "poly_us_order_half_even_v1":
        total = Decimal(state.get("exact", "0")) + exact
        paid = Decimal(state.get("paid", "0"))
        result = min(exact.quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN),
                     total.quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN) - paid)
        if result < 0:
            raise ValueError("INVALID_FEE_ACCUMULATOR")
        state.update(exact=str(total), paid=str(paid + result))
        return result
    if rounding == "kalshi_order_balance_v1":
        precision = Decimal(str(contract.get("balance_precision", "NaN")))
        if precision not in (Decimal(".0001"), Decimal(".01")) or side not in ("BUY", "SELL"):
            raise ValueError("UNKNOWN_ACCOUNT_BALANCE_PRECISION")
        trade = exact.quantize(Decimal(".000001"), rounding=ROUND_CEILING)
        revenue = Decimal(str(quantity)) * Decimal(str(price)) * (-1 if side == "BUY" else 1)
        rounding_fee = revenue - trade - (revenue - trade).quantize(precision, rounding=ROUND_FLOOR)
        accumulated = Decimal(state.get("rounding", "0")) + rounding_fee
        rebate = min(accumulated, trade + rounding_fee).quantize(precision, rounding=ROUND_FLOOR)
        state["rounding"] = str(accumulated - rebate)
        return trade + rounding_fee - rebate
    return Decimal(str(frozen_fee(quantity, price, contract)))


def reserve(quantity, contract):
    """Bound all possible taker fill prices and fragmentation before sending an order."""
    maximum = model(quantity, ".5", contract)
    rounding = contract.get("fee_rounding")
    if rounding == "poly_us_order_half_even_v1":
        return maximum.quantize(Decimal(".01"), rounding=ROUND_HALF_EVEN)
    step = Decimal(str(contract["quantity_step"]))
    if not step.is_finite() or step <= 0:
        raise ValueError("INVALID_QUANTITY_STEP")
    fills = (Decimal(str(quantity)) / step).to_integral_value(rounding=ROUND_CEILING)
    if rounding == "kalshi_order_balance_v1":
        precision = Decimal(str(contract.get("balance_precision", "NaN")))
        if precision not in (Decimal(".0001"), Decimal(".01")):
            raise ValueError("UNKNOWN_ACCOUNT_BALANCE_PRECISION")
        return maximum + fills * (precision + Decimal(".000001"))
    if rounding == "ceil_cent":
        return maximum + fills * Decimal(".01")
    if rounding == "exact":
        return maximum
    raise ValueError("UNKNOWN_FEE_ROUNDING")

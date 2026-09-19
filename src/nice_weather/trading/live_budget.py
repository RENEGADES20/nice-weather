"""Non-replenishing per-venue acceptance budget, committed before any HTTP write."""

from decimal import ROUND_CEILING, Decimal

LIMIT = Decimal("5")


def install(con):
    con.execute("""CREATE TABLE IF NOT EXISTS live_test_budget (
        venue TEXT NOT NULL, account TEXT NOT NULL, request_id TEXT NOT NULL,
        reserved TEXT NOT NULL, spent TEXT NOT NULL DEFAULT '0',
        terminal INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(account,request_id))""")


def reserve(con, venue, account, request_id, contract, order):
    from nice_weather.trading.us_transport import amount

    if order.get("kind") == "cancel":
        return
    if not contract.get("fee_known"):
        raise ValueError("Unknown fees; live budget cannot be reserved")
    q = amount(order["quantity"], contract["quantity_step"])
    p = amount(order["price"], contract["tick_size"], upper=1)
    step = amount(contract["quantity_step"], contract["quantity_step"])
    rate, exponent = Decimal(str(contract["fee_rate"])), Decimal(str(contract["fee_exponent"]))
    if not rate.is_finite() or rate < 0 or not exponent.is_finite() or exponent < 0:
        raise ValueError("Invalid fee schedule")
    fees = q * rate * Decimal(".25") ** exponent
    if contract.get("fee_rounding") in {"poly_us_order_half_even_v1", "kalshi_order_balance_v1"}:
        from nice_weather.trading.us_fees import reserve as fee_reserve

        fees = fee_reserve(q, contract)
    elif contract.get("fee_rounding") == "ceil_cent":
        # Allow every minimum quantity execution to round its own fee upward.
        fees += (q / step).to_integral_value(rounding=ROUND_CEILING) * Decimal(".01")
    elif contract.get("fee_rounding") != "exact":
        raise ValueError("Unknown fee rounding")
    required = (p * q if order["side"] == "BUY" else Decimal(0)) + fees
    rows = con.execute("SELECT reserved,spent FROM live_test_budget WHERE venue=?", (venue,))
    used = sum((Decimal(r["reserved"]) + Decimal(r["spent"]) for r in rows), Decimal(0))
    if used + required > LIMIT:
        raise ValueError("Per-venue cumulative $5 acceptance budget exceeded")
    con.execute(
        "INSERT INTO live_test_budget (venue,account,request_id,reserved) VALUES (?,?,?,?)",
        (venue, account, request_id, str(required)),
    )


def rejected(con, account, request_id):
    con.execute(
        "UPDATE live_test_budget SET reserved='0',terminal=1 WHERE account=? AND request_id=?",
        (account, request_id),
    )


def reconcile(con, account, request_id, cumulative_spent, remaining_reserve, *, terminal):
    """Caller supplies native reconciled facts; proceeds must never subtract from spent."""
    spent, remaining = Decimal(str(cumulative_spent)), Decimal(str(remaining_reserve))
    row = con.execute(
        "SELECT * FROM live_test_budget WHERE account=? AND request_id=?", (account, request_id)
    ).fetchone()
    if (
        row is None
        or not spent.is_finite()
        or not remaining.is_finite()
        or spent < Decimal(row["spent"])
        or remaining < 0
        or (terminal and remaining != 0)
        or (row["terminal"] and not terminal)
    ):
        raise ValueError("Invalid cumulative budget reconciliation")
    if spent + remaining > Decimal(row["spent"]) + Decimal(row["reserved"]):
        raise ValueError("Actual cost exceeds reserved bound; freeze account for reconciliation")
    con.execute(
        "UPDATE live_test_budget SET spent=?,reserved=?,terminal=? "
        "WHERE account=? AND request_id=?",
        (str(spent), str(remaining), int(terminal), account, request_id),
    )

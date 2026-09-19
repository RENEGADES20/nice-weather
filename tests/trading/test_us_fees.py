from decimal import Decimal

import pytest

from nice_weather.trading.us_fees import charge, reserve


def schedule(rounding, **extra):
    return dict(fee_known=True, fee_rate=".0695", fee_exponent=1,
                fee_rounding=rounding, quantity_step=".01", **extra)


def test_poly_order_cap_and_half_even_survive_serialization():
    import json

    contract = schedule("poly_us_order_half_even_v1")
    state = {}
    assert charge(1, ".5", contract, state) == Decimal(".02")
    state = json.loads(json.dumps(state))
    assert charge(1, ".5", contract, state) == Decimal(".01")
    assert charge(1, ".5", contract, state) == Decimal(".02")
    assert Decimal(state["paid"]) == Decimal(".05")
    assert reserve(3, contract) == Decimal(".05")
    contract["fee_rate"] = ".1"
    assert charge(1, ".5", contract) == Decimal(".02")
    contract["fee_rate"] = ".14"
    assert charge(1, ".5", contract) == Decimal(".04")


def test_kalshi_documented_balance_example_and_missing_precision():
    contract = schedule("kalshi_order_balance_v1", balance_precision=".01")
    # q=.1, p=.55, coefficient=.147 gives documented model fee .00363825.
    contract["fee_rate"] = ".147"
    state = {}
    assert charge(".1", ".55", contract, state) == Decimal(".005")
    assert Decimal(state["rounding"]) == Decimal(".001361")
    del contract["balance_precision"]
    with pytest.raises(ValueError, match="PRECISION"):
        charge(1, ".5", contract)
    with pytest.raises(ValueError, match="PRECISION"):
        reserve(1, contract)

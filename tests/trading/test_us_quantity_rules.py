import json

import pytest

from nice_weather.trading.us_markets import normalize


def market(minimum):
    return {"slug": "tc-temp-nychigh-2026-09-22-gte70", "title": "70 or above",
            "description": "Central Park (KNYC) Climatological Report",
            "feeCoefficient": 0.0695, "orderPriceMinTickSize": 0.01,
            "endDate": "2026-09-23T05:00:00Z", "active": True, "closed": False,
            "minimumTradeQty": minimum}


def contract(raw):
    return normalize("poly_us", "2026-09-22", {"event": {"markets": [raw]}}, 1)[0]


@pytest.mark.parametrize("minimum,step", [(0.01, "0.01"), (1, "1"), (2, "1")])
def test_market_specific_quantity_rule_keeps_weather_gate(minimum, step):
    c = contract(market(minimum))
    assert c["minimum_order_size"] == minimum
    assert c["quantity_step"] == step
    assert c["parse_status"] == "ambiguous"
    reasons = json.loads(c["ambiguities_json"])
    assert len(reasons) == 1
    assert "observation window" in reasons[0]
    assert "rounding" in reasons[0] and "revision" in reasons[0]


@pytest.mark.parametrize("minimum", [None, True, False, 0, -1, float("nan"), float("inf")])
def test_invalid_quantity_fact_is_never_replaced_with_default(minimum):
    with pytest.raises(ValueError, match="fees/minimum"):
        contract(market(minimum))


def test_missing_quantity_and_changed_rule_version():
    raw = market(0.01)
    old = contract(raw)
    raw.pop("minimumTradeQty")
    with pytest.raises(ValueError, match="fees/minimum"):
        contract(raw)
    assert old["rules_version"] != contract(market(1))["rules_version"]

import json
from datetime import datetime

import pytest

from nice_weather.trading.us_markets import POLY_WEATHER_AUDITED_AT, normalize


def raw_market(day="2026-09-22", title="69 to 70", condition="between 69F and 70F"):
    from datetime import timedelta
    end = (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat()
    return {
        "slug": f"tc-temp-nychigh-{day}-gte69lt70f", "title": title,
        "description": (
            "Will the highest temperature recorded at Central Park (KNYC) in New York City "
            f"for {day} "
            "as reported by the National Weather Service's Climatological Report (Daily) be "
            + condition + "? Outcome verified from NWS Climatological Report."
        ),
        "outcomes": '["Yes","No"]', "endDate": end + "T05:00:00Z",
        "minimumTradeQty": .01, "orderPriceMinTickSize": .01, "feeCoefficient": .0695,
        "active": True, "closed": False, "status": "MARKET_STATUS_OPEN", "ep3Status": "OPEN",
    }


def normalized(raw, day="2026-09-22", received=POLY_WEATHER_AUDITED_AT):
    return normalize("poly_us", day, {"event": {"markets": [raw]}}, received)[0]


@pytest.mark.parametrize("title,condition", [
    ("68 or below", "less than or equal to 68F"),
    ("69 to 70", "between 69F and 70F"),
    ("77 or above", "greater than or equal to 77F"),
])
def test_exact_audited_terms_cover_tail_and_inclusive_adjacent_bins(title, condition):
    c = normalized(raw_market(title=title, condition=condition))
    assert c["parse_status"] == "parsed"
    assert json.loads(c["ambiguities_json"]) == []
    assert c["rules_audit"]["rounding"] == "source_reported_no_local_rerounding"
    assert c["rules_audit"]["finality"] == "exchange_settlement_required"
    assert c["settlement_source"] == "nws_cli"
    assert c["accepting_orders"] is True


@pytest.mark.parametrize("change", [
    {"title": "70 to 71"}, {"endDate": "2026-09-21T04:00:00Z"},
    {"endDate": "2026-09-23T05:00:00"}, {"outcomes": '["No","Yes"]'},
    {"outcomes": None},
])
def test_conflicting_market_fields_keep_no_trade(change):
    assert normalized(raw_market() | change)["parse_status"] == "ambiguous"


def test_rule_knowledge_is_not_backdated_and_prose_changes_invalidate_it():
    raw = raw_market()
    old = normalized(raw, received=POLY_WEATHER_AUDITED_AT - 1)
    new = normalized(raw)
    assert old["parse_status"] == "ambiguous"
    assert old["rules_version"] != new["rules_version"]
    for old_text, new_text in (("2026-09-22", "2026-09-21"), ("70F", "71F"),
                               ("highest", "lowest"), ("69F", "69C")):
        changed = raw | {"description": raw["description"].replace(old_text, new_text)}
        assert normalized(changed)["parse_status"] == "ambiguous"


@pytest.mark.parametrize("day", ["2026-11-01", "2027-03-14"])
def test_climate_day_remains_24_hours_across_new_york_dst_changes(day):
    c = normalized(raw_market(day), day)
    assert c["parse_status"] == "parsed"
    start, end = map(datetime.fromisoformat, (c["observation_start"], c["observation_end"]))
    assert (end - start).total_seconds() == 86400
    assert start.hour == end.hour == 5


@pytest.mark.parametrize("hour", [4, 9])
def test_api_close_does_not_redefine_climate_observation_period(hour):
    c = normalized(raw_market() | {"endDate": f"2026-09-23T{hour:02d}:00:00Z"})
    assert c["parse_status"] == "parsed"
    assert datetime.fromisoformat(c["observation_end"]).hour == 5
    assert datetime.fromisoformat(c["close_time"]).hour == hour


@pytest.mark.parametrize("change", [{"ep3Status": "HALTED"}, {"status": "MARKET_STATUS_CLOSED"}])
def test_audited_rules_do_not_override_exchange_trading_state(change):
    c = normalized(raw_market() | change)
    assert c["parse_status"] == "parsed"
    assert c["accepting_orders"] is False

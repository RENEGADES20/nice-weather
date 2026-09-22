import pytest
from nautilus_trader.accounting.accounts.margin import MarginAccount
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.identifiers import AccountId

from nice_weather.trading.us_reports import account_state

NOW = 1790056000000000000


@pytest.mark.parametrize("venue,payload,field,value", [
    ("kalshi", {"balance_dollars": "7.0001", "portfolio_value": 130,
                "updated_ts": 1790055999}, "available_balance_usd", "7.0001"),
    ("poly_us", {"balances": [{"currency": "USD", "currentBalance": "5.70",
                              "buyingPower": "4.70", "marginRequirement": 1,
                              "lastUpdated": None}]}, "currentBalance", "5.70"),
])
def test_native_readonly_account_preserves_facts_without_zero_balances(
    venue, payload, field, value,
):
    state = account_state(venue, AccountId(venue.upper() + "-knyc"), payload, NOW)
    state = AccountState.from_dict(AccountState.to_dict(state))
    native = MarginAccount(state, calculate_account_state=False)
    assert state.info["venue_balance_facts"][field] == value
    assert state.info["trading_enabled"] is False
    assert state.info["reconciled"] is False
    assert native.balance_total() is None
    assert native.balance_free() is None
    assert native.balance_locked() is None
    assert state.ts_init == NOW
    if venue == "poly_us":
        assert state.ts_event == 0
        assert state.info["source_time_known"] is False
    else:
        assert state.ts_event == 1790055999000000000


@pytest.mark.parametrize("change", [
    {"currentBalance": True}, {"buyingPower": "NaN"},
    {"lastUpdated": ""}, {"lastUpdated": "2026-09-23T00:00:00Z"},
    {"currency": "USDC"},
])
def test_invalid_balance_facts_do_not_register_an_account(change):
    row = {"currency": "USD", "currentBalance": 0, "buyingPower": 0, "lastUpdated": None}
    with pytest.raises(ValueError):
        account_state("poly_us", AccountId("POLY_US-knyc"), {"balances": [row | change]}, NOW)


def test_duplicate_currency_and_account_scope_are_rejected():
    row = {"currency": "USD", "currentBalance": 0, "buyingPower": 0}
    with pytest.raises(ValueError):
        account_state("poly_us", AccountId("POLY_US-knyc"), {"balances": [row, row]}, NOW)
    with pytest.raises(ValueError):
        account_state("poly_us", AccountId("KALSHI-knyc"), {"balances": [row]}, NOW)

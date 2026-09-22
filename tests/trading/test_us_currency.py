import subprocess
import sys

import pytest

from nice_weather.trading.us_currency import register_live_usd


@pytest.mark.parametrize("venue,precision", [("kalshi", 4), ("kalshi", 2), ("poly_us", 2)])
def test_isolated_live_currency_survives_native_account_roundtrip(venue, precision):
    # A child is essential: both registries are global and Paper must stay USD2.
    program = """
from decimal import Decimal
from nautilus_trader.core import nautilus_pyo3 as rust
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.objects import AccountBalance, Money
from nice_weather.trading.us_currency import register_live_usd
import sys
venue, precision = sys.argv[1], int(sys.argv[2])
usd = register_live_usd(venue, precision)
unit = Decimal(1).scaleb(-precision)
amount = Money(unit, usd)
assert Money.from_str(str(amount)).as_decimal() == unit
assert Decimal(str(rust.Money.from_str(str(amount))).split()[0]) == unit
event = AccountState(
    account_id=AccountId(venue.upper() + '-test'), account_type=AccountType.MARGIN,
    base_currency=usd, reported=True,
    balances=[AccountBalance(Money(unit * 3, usd), amount, Money(unit * 2, usd))],
    margins=[], info={}, event_id=UUID4(), ts_event=1, ts_init=2,
)
restored = AccountState.from_dict(AccountState.to_dict(event))
assert restored.balances[0].total.as_decimal() == unit * 3
assert restored.balances[0].locked.as_decimal() == unit
assert restored.balances[0].free.as_decimal() == unit * 2
assert restored.base_currency.precision == precision
assert register_live_usd(venue, precision).precision == precision
try:
    register_live_usd('kalshi' if venue == 'poly_us' else 'poly_us', 2)
except ValueError:
    pass
else:
    raise AssertionError('Must isolate venues in separate processes')
"""
    subprocess.run([sys.executable, "-c", program, venue, str(precision)], check=True,
                   capture_output=True, text=True, timeout=60)
    from nautilus_trader.model.objects import Currency

    assert Currency.from_str("USD").precision == 2


@pytest.mark.parametrize("venue,precision", [
    ("poly_intl", 2), ("poly_us", 4), ("kalshi", True), ("kalshi", "4"), ("kalshi", 6),
])
def test_invalid_currency_profile_does_not_mutate_registry(venue, precision):
    from nautilus_trader.model.objects import Currency

    with pytest.raises(ValueError):
        register_live_usd(venue, precision)
    assert Currency.from_str("USD").precision == 2

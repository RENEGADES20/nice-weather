"""USD bootstrap for a dedicated US Live process, before constructing native state.

Nautilus currency registries are process-global. Never call this in Paper, replay,
the API process, or after loading cached accounts. Importing this module is inert.
The account's verified precision must be supplied; it is not inferred from cash.
"""

_profile = None


def register_live_usd(venue: str, precision: int):
    global _profile

    if type(precision) is not int or precision not in {2, 4, 6}:
        raise ValueError("Unverified USD precision")
    if venue not in {"kalshi", "poly_us"} or (venue == "poly_us" and precision != 2):
        raise ValueError("Unsupported venue currency profile")
    if _profile is not None and _profile != (venue, precision):
        raise ValueError("Live currency profile cannot change within a process")

    from nautilus_trader.core import nautilus_pyo3
    from nautilus_trader.model.enums import CurrencyType
    from nautilus_trader.model.objects import Currency

    currency = Currency("USD", precision, 840, "US Dollar", CurrencyType.FIAT)
    rust_currency = nautilus_pyo3.Currency(
        "USD", precision, 840, "US Dollar", nautilus_pyo3.CurrencyType.FIAT
    )
    Currency.register(currency, overwrite=True)
    nautilus_pyo3.Currency.register(rust_currency, overwrite=True)
    _profile = (venue, precision)
    return currency

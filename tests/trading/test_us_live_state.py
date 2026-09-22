from decimal import Decimal

import pytest

from nice_weather.trading.live_budget import install as install_budget
from nice_weather.trading.live_budget import reconcile, reserve
from nice_weather.trading.storage import connect
from nice_weather.trading.us_live_state import (
    availability,
    budget,
    configure,
    controls,
    install,
    money_facts,
    realized_today,
)


def test_configuration_is_atomic_revision_checked_and_idempotent(tmp_path):
    path = tmp_path / "results.sqlite3"
    install(path)
    payload = {
        "revision": 0,
        "binding": "account-identity",
        "manual_enabled": True,
        "whitelist": ["KNYC"],
    }
    configure(path, "live-poly_us-knyc", "one", payload, "account-identity")
    configure(path, "live-poly_us-knyc", "one", payload, "account-identity")
    assert controls(path, "live-poly_us-knyc")["revision"] == 1
    for patch in (
        {"revision": 0},
        {"revision": 1, "binding": "other"},
        {"revision": 1, "manual_enabled": "yes"},
        {"revision": 1, "order_limit": "NaN"},
        {"revision": 1, "order_limit": "5.01"},
    ):
        with pytest.raises(ValueError):
            configure(path, "live-poly_us-knyc", "two", patch, "account-identity")
    assert controls(path, "live-poly_us-knyc")["revision"] == 1


def test_amounts_remain_separate_and_unknown_is_not_zero():
    funds = money_facts(
        "poly_us",
        {
            "balances": [
                {
                    "currency": "USD",
                    "currentBalance": "50",
                    "buyingPower": "50",
                    "balanceReservation": "50",
                    "openOrders": "0",
                }
            ]
        },
    )
    assert funds["cash"] == funds["available"] == funds["reserved"] == "50"
    assert funds["order_notional"] == "0" and funds["position_value"] is None
    funds = money_facts(
        "kalshi",
        {
            "balance_dollars": "1.0001",
            "portfolio_value": 50,
            "balance_breakdown": [
                {"exchange_index": 0, "balance": ".1"},
                {"exchange_index": 3, "balance": ".9001"},
            ],
        },
    )
    assert funds["available"] == "1.0001"
    assert funds["cash"] is None and funds["reserved"] is None
    assert funds["position_value"] == "0.5"


def test_unknown_order_blocks_new_risk_but_not_stop_or_cancel():
    snapshot = {
        "authenticated": True,
        "received_at": 100,
        "reconciled": False,
        "binding": "same",
        "config": {"binding": "same", "manual_enabled": True},
    }
    assert availability(snapshot, "order", now=101)["allowed"] is False
    assert availability(snapshot, "cancel", now=101)["allowed"] is True
    assert availability(snapshot, "stop", now=200)["allowed"] is True
    assert availability(snapshot, "order", now=111)["reason"] == "ACCOUNT_STALE"


def test_exact_budget_limit_fees_and_restart_do_not_replenish(tmp_path):
    path = tmp_path / "results.sqlite3"
    contract = {
        "fee_known": True,
        "quantity_step": ".01",
        "tick_size": ".01",
        "fee_rate": "0",
        "fee_exponent": 1,
        "fee_rounding": "exact",
    }
    order = {"quantity": "10", "price": ".5", "side": "BUY"}
    with connect(path) as con:
        install_budget(con)
        reserve(con, "poly_us", "first", "one", contract, order)
    assert budget(path, "poly_us")["remaining"] == "0"
    with connect(path) as con:
        with pytest.raises(ValueError, match="budget exceeded"):
            reserve(con, "poly_us", "other", "two", contract, order)
        reconcile(con, "first", "one", "5", 0, terminal=True)
        reserve(con, "poly_us", "first", "sell", contract, order | {"side": "SELL"})
        reconcile(con, "first", "sell", 0, 0, terminal=True)
        with pytest.raises(ValueError, match="budget exceeded"):
            reserve(con, "poly_us", "new-binding", "three", contract, order)
    assert Decimal(budget(path, "poly_us")["spent"]) == 5


def test_concurrent_accounts_share_one_platform_reservation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / "budget.sqlite3"
    with connect(path) as con:
        install_budget(con)
    contract = dict(fee_known=True, quantity_step="1", tick_size=".01", fee_rate="0",
                    fee_exponent=1, fee_rounding="exact")

    def attempt(account):
        try:
            with connect(path) as con:
                con.execute("BEGIN IMMEDIATE")
                reserve(con, "kalshi", account, account, contract,
                        dict(quantity="6", price=".5", side="BUY"))
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, ["one", "two"])) == [False, True]
    assert Decimal(budget(path, "kalshi")["reserved"]) == 3


def test_realized_loss_uses_execution_day_and_includes_fees():
    from datetime import UTC, datetime
    from types import SimpleNamespace as NS

    def fill(number, day, side, qty, px, fee):
        def amount(value):
            return NS(as_decimal=lambda: Decimal(value))

        return NS(instrument_id="market", trade_id=NS(value=str(number)),
                  ts_event=int(datetime(2026, 9, day, 16, tzinfo=UTC).timestamp() * 1e9),
                  order_side=NS(name=side), last_qty=amount(qty), last_px=amount(px),
                  commission=amount(fee))

    rows = [fill(1, 21, "BUY", "2", ".6", ".01"),
            fill(2, 22, "SELL", "3", ".4", ".02"),
            fill(3, 22, "BUY", "1", ".3", ".01")]
    assert realized_today(rows, datetime(2026, 9, 22).date()) == Decimal("-.33")

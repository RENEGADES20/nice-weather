from decimal import Decimal

import pytest
from test_us_transport import contract

from nice_weather.trading.live_budget import install, reconcile, rejected, reserve
from nice_weather.trading.storage import connect


def test_budget_survives_accounts_restart_and_does_not_credit_proceeds(tmp_path):
    path = tmp_path / "budget.sqlite3"
    c = contract("poly_us")
    order = {"quantity": 1, "price": 0.4, "side": "BUY"}
    with connect(path) as con:
        install(con)
        con.execute("BEGIN IMMEDIATE")
        reserve(con, "poly_us", "first", "a", c, order)
        reconcile(con, "first", "a", ".42", 0, terminal=True)
        with pytest.raises(ValueError):
            reconcile(con, "first", "a", 0, 0, terminal=True)
    with connect(path) as con:
        con.execute("BEGIN IMMEDIATE")
        reserve(con, "poly_us", "second", "b", c, order | {"quantity": 3})
        with pytest.raises(ValueError, match="budget exceeded"):
            reserve(con, "poly_us", "third", "c", c, order)
        rejected(con, "second", "b")
        reserve(con, "poly_us", "third", "c", c, order)
        assert Decimal(
            con.execute("SELECT spent FROM live_test_budget WHERE request_id='a'").fetchone()[0]
        ) == Decimal(".42")


def test_invalid_budget_evidence_fails_closed(tmp_path):
    with connect(tmp_path / "budget.sqlite3") as con:
        install(con)
        c = contract("kalshi")
        reserve(con, "kalshi", "a", "one", c, {"quantity": 1, "price": 0.4, "side": "BUY"})
        for spent, remaining, terminal in (("NaN", 0, True), (0, 1, True), (10, 0, True)):
            with pytest.raises(ValueError):
                reconcile(con, "a", "one", spent, remaining, terminal=terminal)

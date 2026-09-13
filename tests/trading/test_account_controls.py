import json

import pytest
from test_native_workbench import buy, contract, event
from test_terminal import book

from nice_weather.trading.metrics import pnl_view
from nice_weather.trading.recovery import PaperRunner
from nice_weather.trading.storage import Results, connect
from nice_weather.trading.worker import run_config


def test_cash_edit_trade_recovery_reset_and_idempotency(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    config = run_config("sandbox-test", "sandbox") | {"started_ns": event("clock", 0)["ts"]}
    results.create("run", "sandbox-test", "sandbox", config)
    runner = PaperRunner(results, results.run("run"))
    runner.apply("contract", event("contract", 0, contract(fee_rate=0.02)))
    runner.apply("depth", event("depth", 1, book()))
    runner.apply("buy", event("order", 2, buy(quantity=2)))
    before = runner.session.snapshot()
    change = event("balance", 3, {"cash": 200, "expected_revision": before["account_revision"]})
    after = runner.apply("balance", change)
    assert after["cash"] == 200
    assert after["total_pnl"] == pytest.approx(before["total_pnl"])
    assert runner.apply("balance", change) == after
    with connect(results.path, readonly=True) as con:
        samples = [dict(r) for r in con.execute("SELECT * FROM paper_equity ORDER BY ts")]
    view = pnl_view(
        samples, 100, after["fills"], config["started_ns"], runner.session.config["funding_events"]
    )
    assert view["points"][-1]["value"] == pytest.approx(after["total_pnl"])
    runner.session.dispose()
    runner = PaperRunner(results, results.run("run"))
    assert runner.session.snapshot()["cash"] == 200
    runner.apply("depth2", event("depth", 4, book()))
    result = runner.apply("sell", event("order", 5, buy(side="SELL", price=0.39, quantity=2)))
    assert result["cash"] > 200 and not result["positions"]
    # A dialog opened before the fill cannot reset a newer account unnoticed.
    rejected = runner.apply(
        "stale", event("reset", 6, {"cash": 500, "expected_revision": before["account_revision"]})
    )
    assert "Account changed" in rejected["rejections"][-1]["reason"]
    revision = rejected["account_revision"]
    reset = event("reset", 7, {"cash": 500, "expected_revision": revision})
    fresh = runner.apply("reset", reset)
    assert fresh["cash"] == 500 and fresh["total_pnl"] == 0
    assert not fresh["positions"] and not fresh["orders"] and not fresh["fills"]
    assert not fresh["strategy_enabled"]
    runner.apply("reset", reset)
    runner.session.dispose()
    runner = PaperRunner(results, results.run("run"))
    assert runner.session.snapshot()["cash"] == 500
    assert runner.session.snapshot()["total_pnl"] == 0
    with connect(results.path, readonly=True) as con:
        archives = list(con.execute("SELECT body FROM paper_resets"))
        assert len(archives) == 1
        assert json.loads(archives[0][0])["orders"]
        assert '"bids"' not in archives[0][0] and '"asks"' not in archives[0][0]
    runner.session.dispose()


@pytest.mark.parametrize("cash", [float("nan"), -1, 1_000_001, 0.0000001])
def test_invalid_cash_never_changes_account(tmp_path, cash):
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", "sandbox-test", "sandbox", run_config("sandbox-test", "sandbox"))
    runner = PaperRunner(results, results.run("run"))
    before = runner.session.snapshot()
    result = runner.apply(
        "invalid",
        event("balance", 1, {"cash": cash, "expected_revision": before["account_revision"]}),
    )
    assert result["cash"] == 100 and result["rejections"]
    runner.session.dispose()

import json

import pytest
from test_native_workbench import T, buy, contract, event

from nice_weather.trading.recovery import PaperRunner, native_state, restore
from nice_weather.trading.storage import Results, connect
from nice_weather.trading.worker import run_config


def book():
    return dict(
        token_id="111",
        valid=True,
        received_ns=T + 1,
        bids=[["0.39", "10"], ["0.38", "10"]],
        asks=[["0.40", "2"], ["0.41", "3"]],
    )


def test_cross_depth_restore_and_no_book_storage(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    results.create(
        "run",
        "sandbox-test",
        "sandbox",
        run_config("sandbox-test", "sandbox") | {"execution_version": 3},
    )
    runner = PaperRunner(results, results.run("run"))
    runner.apply("contract", event("contract", 0, contract()))
    runner.apply("depth", event("depth", 1, book()))
    runner.apply("buy", event("order", 2, buy(price=0.41, quantity=5)))
    before = runner.session.snapshot()
    assert sum(f["quantity"] for f in before["fills"]) == 5
    assert before["cash"] == pytest.approx(97.97)
    state = native_state(runner.session)
    runner.session.dispose()
    restored = restore(state)
    after = restored.snapshot()
    assert after["cash"] == before["cash"]
    assert after["fees"] == before["fees"]
    assert after["fills"] == before["fills"]
    assert after["positions"][0]["quantity"] == 5
    restored.dispose()
    with connect(results.path, readonly=True) as con:
        assert con.execute("SELECT COUNT(*) FROM inputs").fetchone()[0] == 0
        saved = con.execute("SELECT body FROM paper_state").fetchone()[0]
        assert '"bids"' not in saved and '"asks"' not in saved and "source_payload" not in saved
        assert json.loads(saved)["config"]["execution_version"] == 3


def test_restart_resume_sell_and_receipts(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", "sandbox-test", "sandbox", run_config("sandbox-test", "sandbox"))
    runner = PaperRunner(results, results.run("run"))
    runner.apply("contract", event("contract", 0, contract(fee_rate=0.02)))
    runner.apply("depth", event("depth", 1, book()))
    runner.apply("buy", event("order", 2, buy(price=0.41, quantity=5)))
    before = runner.session.snapshot()
    runner.session.dispose()
    runner = PaperRunner(results, results.run("run"))
    runner.apply("buy", event("order", 3, buy(price=0.41, quantity=5)))
    assert runner.session.snapshot()["fills"] == before["fills"]
    runner.apply("depth2", event("depth", 4, book()))
    runner.apply("sell", event("order", 5, buy(side="SELL", price=0.39, quantity=5)))
    after = runner.session.snapshot()
    assert after["positions"] == []
    assert after["cash"] < 100
    assert len(after["fills"]) == len(before["fills"]) + 1
    runner.session.dispose()
    runner = PaperRunner(results, results.run("run"))
    assert runner.session.snapshot()["cash"] == after["cash"]
    assert runner.session.snapshot()["fees"] == after["fees"]
    runner.session.dispose()


def test_identical_depth_fok_and_bounded_samples(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", "sandbox-test", "sandbox", run_config("sandbox-test", "sandbox"))
    runner = PaperRunner(results, results.run("run"))
    runner.apply("contract", event("contract", 0, contract()))
    runner.apply("depth", event("depth", 1, book()))
    runner.apply("fok", event("order", 2, buy(price=0.41, quantity=6, tif="FOK")))
    assert runner.session.snapshot()["fills"] == []
    runner.apply("buy", event("order", 3, buy(price=0.41, quantity=5)))
    runner.apply("same-depth", event("depth", 4, book()))
    runner.apply("again", event("order", 5, buy(price=0.41, quantity=5)))
    assert sum(f["quantity"] for f in runner.session.snapshot()["fills"]) == 5
    reduced = book() | {"asks": [["0.40", "1"], ["0.41", "2"]]}
    runner.apply("reduced", event("depth", 6, reduced))
    runner.apply("reuse", event("order", 7, buy(price=0.41, quantity=5)))
    assert sum(f["quantity"] for f in runner.session.snapshot()["fills"]) == 5
    with connect(results.path) as con:
        count = con.execute("SELECT COUNT(*) FROM paper_equity").fetchone()[0]
    for second in range(1, 91):
        runner.apply(f"clock-{second}", event("clock", second * 1_000_000_000))
    with connect(results.path) as con:
        assert con.execute("SELECT COUNT(*) FROM paper_equity").fetchone()[0] - count <= 4
    runner.session.dispose()


def test_resting_restart_and_settlement_facts(tmp_path):
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", "sandbox-test", "sandbox", run_config("sandbox-test", "sandbox"))
    runner = PaperRunner(results, results.run("run"))
    runner.apply("contract", event("contract", 0, contract()))
    runner.apply("depth", event("depth", 1, book()))
    runner.apply("rest", event("order", 2, buy(price=0.38, quantity=2, tif="GTC")))
    assert runner.session.open_orders()
    runner.session.dispose()
    runner = PaperRunner(results, results.run("run"))
    assert not runner.session.open_orders()
    assert runner.session.snapshot()["available"] == 100
    assert "Restart cancelled" in runner.session.rejections[-1]["reason"]
    runner.apply("depth2", event("depth", 4, book()))
    runner.apply("buy", event("order", 5, buy(price=0.41, quantity=5)))
    runner.apply(
        "settle",
        event("settlement", 6, {"token_id": "111", "value": 1, "evidence_type": "official_final"}),
    )
    before = runner.session.snapshot()
    assert before["positions"] == []
    runner.session.dispose()
    runner = PaperRunner(results, results.run("run"))
    assert runner.session.snapshot()["cash"] == before["cash"]
    assert runner.session.snapshot()["fills"] == before["fills"]
    assert runner.session.settlements[0]["source_hash"]
    runner.session.dispose()

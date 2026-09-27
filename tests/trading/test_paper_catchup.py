import json
import sqlite3
from contextlib import contextmanager

import pytest
from test_knyc_terminal import scenario
from test_paper_approximation import make_session, order, quote

from nice_weather.trading import recovery, storage
from nice_weather.trading.recovery import PaperRunner, native_state, restore
from nice_weather.trading.storage import Results, connect, digest


@pytest.fixture
def large_runner(tmp_path):
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import AccountState

    session = make_session("poly_us")
    quote(session)
    order(session, quantity=2)
    quote(session, bid=.6, ask=.7)
    order(session, "exit", "SELL", .5, 2)
    account = session.engine.cache.account_for_venue(session.venue)
    event = AccountState.to_dict(account.last_event)
    for _ in range(2000):
        account.apply(AccountState.from_dict(event | {"event_id": str(UUID4())}))
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", session.config["account"], "sandbox", session.config)
    runner = PaperRunner(results, results.run("run"))
    runner.session.dispose()
    runner.session = session
    session.config["feed_cursor"] = 10
    runner.commit("fixture")
    try:
        yield runner
    finally:
        session.dispose()


def saved(runner):
    with connect(runner.results.path, readonly=True) as con:
        row = con.execute("SELECT body,checksum FROM paper_state WHERE run_id='run'").fetchone()
        return dict(row)


def test_large_account_history_is_cached_and_each_checkpoint_encoded_once(
    large_runner, monkeypatch
):
    from nautilus_trader.accounting.accounts.cash import CashAccount
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import AccountState

    runner = large_runner
    session = runner.session
    account = session.engine.cache.account_for_venue(session.venue)
    original = CashAccount.to_dict(account)
    cached = runner._account_cache["state"]
    encoded_states = []
    encode = storage.encoded

    def count_encoding(value):
        if isinstance(value, dict) and {"positions", "account", "config"} <= value.keys():
            encoded_states.append(value)
        return encode(value)

    monkeypatch.setattr(storage, "encoded", count_encoding)
    monkeypatch.setattr(recovery, "encoded", count_encoding)
    for cursor in range(11, 19):
        quote(session)
        session.config["feed_cursor"] = cursor
        runner.commit()
        assert runner._account_cache["state"] is cached
    assert len(encoded_states) == 8
    checkpoint = saved(runner)
    state = json.loads(checkpoint["body"])
    assert digest(state) == checkpoint["checksum"]
    assert state["account"] == original
    assert state["config"]["feed_cursor"] == 18
    assert len(state["account"]["events"]) >= 2000
    # Public callers receive fresh account dictionaries and cannot alter the commit memo.
    native_state(session)["account"]["events"].clear()
    assert native_state(session)["account"] == original
    assert runner._account_cache["state"] == original

    # Same total cash and timestamp, different native lock state, must invalidate the memo.
    last = AccountState.to_dict(account.last_event)
    balances = [b | {"locked": "0.50", "free": str(float(b["total"]) - .5)}
                for b in last["balances"]]
    account.apply(AccountState.from_dict(last | {
        "event_id": str(UUID4()), "balances": balances, "reported": True}))
    runner.commit("account-lock")
    assert runner._account_cache["state"] is not cached
    assert json.loads(saved(runner)["body"])["account"] == CashAccount.to_dict(account)
    assert session.snapshot()["cash"] == float(last["balances"][0]["total"])


def test_continuous_strategy_reads_native_total_cash_without_full_snapshot(monkeypatch):
    from nice_weather.trading import signals_v2

    session = make_session("poly_us", strategy_id="S1_S2_S3", signal_version="knyc-executable-v2")
    try:
        quote(session)
        order(session, "resting", price=.2, quantity=10, tif="GTC")
        before = session.snapshot()
        risks = []
        evaluate = signals_v2.evaluate

        def record(strategy, contracts, weather, books, now, risk):
            risks.append(risk)
            return evaluate(strategy, contracts, weather, books, now, risk)

        def unexpected_snapshot():
            raise AssertionError("Risk cash must not rebuild the full historical UI snapshot")

        monkeypatch.setattr(signals_v2, "evaluate", record)
        monkeypatch.setattr(session, "snapshot", unexpected_snapshot)
        session.weather_signal(scenario()[2])
        assert len(risks) == 3
        assert all(risk["cash"] == before["cash"] - before["reserved"] for risk in risks)
        assert set(session.signals) == {"S1", "S2", "S3"}
    finally:
        session.dispose()


def test_failed_checkpoint_keeps_cursor_receipt_and_retry_valid(large_runner, monkeypatch):
    runner = large_runner
    before = saved(runner)
    old_hashes = (runner.last_state_hash, runner.last_price_hash, runner.last_financial_hash)
    old_snapshot = runner.session.snapshot()
    original_connect = recovery.connect

    class FailViewUpdate:
        def __init__(self, con):
            self.con = con

        def execute(self, sql, args=()):
            if sql.startswith("UPDATE runs"):
                raise sqlite3.OperationalError("injected commit failure")
            return self.con.execute(sql, args)

    @contextmanager
    def fail_transaction(path):
        with original_connect(path) as con:
            yield FailViewUpdate(con)

    quote(runner.session, bid=.2, ask=.5)
    runner.session.config["feed_cursor"] = 11
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "connect", fail_transaction)
        with pytest.raises(sqlite3.OperationalError, match="injected"):
            runner.commit("pending")
    assert saved(runner) == before
    assert "pending" not in runner.seen
    assert (runner.last_state_hash, runner.last_price_hash,
            runner.last_financial_hash) == old_hashes
    with connect(runner.results.path, readonly=True) as con:
        assert not con.execute("SELECT 1 FROM paper_receipts WHERE input_id='pending'").fetchone()
    recovered = restore(json.loads(before["body"]))
    try:
        assert recovered.config["feed_cursor"] == 10
        for key in ("cash", "fills", "orders", "signals", "strategy_state"):
            assert recovered.snapshot()[key] == old_snapshot[key]
    finally:
        recovered.dispose()
    runner.commit("pending")
    checkpoint = saved(runner)
    assert json.loads(checkpoint["body"])["config"]["feed_cursor"] == 11
    assert digest(json.loads(checkpoint["body"])) == checkpoint["checksum"]
    assert "pending" in runner.seen


def test_account_cache_accepts_absent_and_replaced_native_account():
    from nice_weather.trading.engine import Session
    from nice_weather.trading.worker import run_config

    session = Session(run_config("sandbox-empty", "sandbox"))
    cache = {}
    try:
        assert native_state(session, account_cache=cache)["account"] is None
        assert native_state(session, account_cache=cache)["account"] is None
        session.apply({"kind": "clock", "ts": 1, "data": {}})
        first = native_state(session, account_cache=cache)["account"]
        assert first is not None
        replacement = Session(run_config("sandbox-other", "sandbox"))
        try:
            replacement.apply({"kind": "clock", "ts": 1, "data": {}})
            assert native_state(replacement, account_cache=cache)["account"] is not first
        finally:
            replacement.dispose()
    finally:
        session.dispose()

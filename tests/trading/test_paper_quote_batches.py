import json

import pytest
from test_paper_approximation import make_session, order, quote

from nice_weather.trading.recovery import PaperRunner, native_state
from nice_weather.trading.storage import Results, connect
from nice_weather.trading.us_runtime import apply_feed_page, feed_event


@pytest.fixture
def runner(tmp_path):
    session = make_session("poly_us")
    results = Results(tmp_path / "results.sqlite3")
    results.create("run", session.config["account"], "sandbox", session.config)
    item = PaperRunner(results, results.run("run"))
    item.session.dispose()
    item.session = session
    session.config["feed_cursor"] = 0
    item.commit("fixture")
    try:
        yield item
    finally:
        session.dispose()


def book(seq, received, *, key="test-1", bid=.3):
    return {"seq": seq, "kind": "book", "key": key, "received": received, "data": {
        "received_at": received, "exchange_time": received - .2, "source": "fixture",
        "bids": [[bid, 5]], "asks": [[.5, 5]], "complete": True}}


def checkpoint(runner):
    with connect(runner.results.path, readonly=True) as con:
        row = con.execute("SELECT * FROM paper_state WHERE run_id='run'").fetchone()
        return json.loads(row["body"])


def record_commits(runner, monkeypatch):
    commits = []
    original = runner.commit

    def record(*args, **kwargs):
        result = original(*args, **kwargs)
        commits.append(runner.session.config["feed_cursor"])
        return result

    monkeypatch.setattr(runner, "commit", record)
    return commits


@pytest.mark.parametrize("count", [1, 15, 16, 17, 33])
def test_idle_quotes_bound_original_events_and_preserve_all_native_applications(
    runner, monkeypatch, count
):
    session = runner.session
    before = native_state(session)
    events = [book(i, session.now / 1e9 + i / 10, bid=.2 + i / 1000)
              for i in range(1, count + 1)]
    applied = []
    apply = session.apply

    def record(event):
        applied.append((event["data"]["token_id"], event["data"]["received_ns"]))
        return apply(event)

    monkeypatch.setattr(session, "apply", record)
    commits = record_commits(runner, monkeypatch)
    apply_feed_page(runner, events)
    assert commits == list(range(16, count + 1, 16)) + ([count] if count % 16 else [])
    assert applied == [(token, int(event["received"] * 1e9)) for event in events
                       for token in ("test-1", "test-1:NO")]
    state = checkpoint(runner)
    assert state["config"]["feed_cursor"] == count
    for key in ("account", "orders", "positions", "signals", "strategy_state", "peak",
                "maximum_drawdown"):
        assert state[key] == before[key]
    assert state["quotes"]["test-1"]["best_bid"] == events[-1]["data"]["bids"][0][0]
    assert state["market_prices"]["test-1"]["known_at"] == events[-1]["received"]


def test_foreign_rows_count_toward_bound_and_nonbook_flushes_previous_cursor(runner, monkeypatch):
    now = runner.session.now / 1e9
    commits = record_commits(runner, monkeypatch)
    rows = [book(1, now + .1)] + [book(i, now + i / 10, key="foreign")
                                 for i in range(2, 17)]
    rows += [book(17, now + 2), {"seq": 18, "kind": "health", "key": "poly_us",
                              "received": now + 3, "data": {}}]
    apply_feed_page(runner, rows)
    assert commits == [16, 17]
    assert checkpoint(runner)["config"]["feed_cursor"] == 17
    assert runner.session.config["feed_cursor"] == 18
    apply_feed_page(runner, [book(19, now + 4, key="foreign")])
    assert commits == [16, 17]


@pytest.mark.parametrize("mode", ["enabled", "position", "order"])
def test_active_financial_sessions_keep_per_event_checkpoints(runner, monkeypatch, mode):
    session = runner.session
    quote(session)
    if mode == "enabled":
        session.enabled = True
    elif mode == "position":
        order(session, quantity=2)
    else:
        order(session, "resting", price=.1, quantity=2, tif="GTC")
    runner.commit("setup")
    commits = record_commits(runner, monkeypatch)
    apply_feed_page(runner, [book(i, session.now / 1e9 + i / 10) for i in range(1, 4)])
    assert commits == [1, 2, 3]


@pytest.mark.parametrize("mutation", ["account", "rejection", "signal", "strategy"])
def test_changes_on_first_leg_force_commit_even_if_second_leg_reverts(
    runner, monkeypatch, mutation
):
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model.events import AccountState

    session = runner.session
    apply = session.apply

    def mutate(event):
        snapshot = apply(event)
        yes = event["data"]["token_id"] == "test-1"
        if mutation == "account" and yes:
            account = session.engine.cache.account_for_venue(session.venue)
            last = AccountState.to_dict(account.last_event)
            account.apply(AccountState.from_dict(last | {"event_id": str(UUID4())}))
        elif mutation == "rejection" and yes:
            session.rejections.append({"reason": "fixture", "ts": session.now})
        elif mutation == "signal":
            session.signals = {"S3": {"events": [{"state": "A"}]}} if yes else {}
        elif mutation == "strategy":
            session.strategy_state = {"fixture": {"signature": "A"}} if yes else {}
        return snapshot

    monkeypatch.setattr(session, "apply", mutate)
    commits = record_commits(runner, monkeypatch)
    apply_feed_page(runner, [book(i, session.now / 1e9 + i / 10) for i in range(1, 4)])
    assert commits == [1, 2, 3]


def test_signal_changes_are_journaled_in_original_order(runner, monkeypatch):
    session = runner.session
    apply = session.apply
    calls = [0]

    def signal(event):
        result = apply(event)
        if event["data"]["token_id"] == "test-1":
            calls[0] += 1
            session.signals = {"S3": {"events": [{"state": "A" if calls[0] % 2 else "B",
                                                "asof": event["ts"]}]}}
        return result

    monkeypatch.setattr(session, "apply", signal)
    commits = record_commits(runner, monkeypatch)
    apply_feed_page(runner, [book(i, session.now / 1e9 + i / 10) for i in range(1, 4)])
    assert commits == [1, 2, 3]
    inputs = [json.loads(row["body"]) for row in runner.results.inputs("run")]
    events = [row["events"][0]["state"] for row in inputs if row["kind"] == "signal-events"]
    assert events == ["A", "B", "A"]


def test_first_event_of_new_minute_and_valuation_changes_flush(runner, monkeypatch):
    session = runner.session
    now = session.now / 1e9
    commits = record_commits(runner, monkeypatch)
    apply_feed_page(runner, [book(1, now + 1), book(2, now + 60), book(3, now + 61)])
    assert commits == [2, 3]
    with connect(runner.results.path, readonly=True) as con:
        minutes = [row[0] for row in con.execute(
            "SELECT ts FROM paper_equity WHERE run_id='run' ORDER BY ts")]
    assert int((now + 60) * 1e9) + 1 in minutes
    runner.last_valid = False
    apply_feed_page(runner, [book(4, now + 62), book(5, now + 63)])
    assert commits == [2, 3, 4, 5]


@pytest.mark.parametrize("failure_seq", [2, 8, 16, 17, 21])
def test_half_book_failure_never_flushes_and_restart_replays_from_durable_cursor(
    runner, monkeypatch, failure_seq
):
    session = runner.session
    now = session.now / 1e9
    rows = [book(i, now + i / 10) for i in range(1, failure_seq + 2)]
    apply = session.apply
    calls = [0]

    def fail(event):
        calls[0] += 1
        if calls[0] == failure_seq * 2:
            raise RuntimeError("injected NO leg failure")
        return apply(event)

    commits = record_commits(runner, monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(session, "apply", fail)
        with pytest.raises(RuntimeError, match="NO leg failure"):
            apply_feed_page(runner, rows)
    durable = ((failure_seq - 1) // 16) * 16
    assert commits == ([16] if durable else [])
    state = checkpoint(runner)
    assert state["config"]["feed_cursor"] == durable
    assert session.config["feed_cursor"] == failure_seq - 1
    restarted = PaperRunner(runner.results, runner.results.run("run"))
    try:
        apply_feed_page(restarted, [row for row in rows if row["seq"] > durable])
        assert checkpoint(restarted)["config"]["feed_cursor"] == rows[-1]["seq"]
        assert restarted.session.snapshot()["cash"] == 100
        assert not restarted.session.snapshot()["fills"]
        assert not restarted.session.snapshot()["orders"]
        assert restarted.session.quotes["test-1"]["best_bid"] == .3
    finally:
        restarted.session.dispose()


def test_batch_and_each_event_have_same_final_public_and_native_facts(runner):
    session = make_session("poly_us")
    try:
        rows = [book(i, session.now / 1e9 + i / 10, bid=.2 + i / 1000)
                for i in range(1, 34)]
        for row in rows:
            for event in feed_event(session, row):
                session.apply(event)
            session.config["feed_cursor"] = row["seq"]
        apply_feed_page(runner, rows)
        for key in ("cash", "fills", "orders", "positions", "equity", "signals",
                    "strategy_state", "max_drawdown"):
            assert runner.session.snapshot()[key] == session.snapshot()[key]
        assert runner.session.quotes == session.quotes
        assert runner.session.market_prices == session.market_prices
    finally:
        session.dispose()


def test_late_transaction_failure_keeps_batch_cursor_and_facts_atomic(runner, monkeypatch):
    import sqlite3
    from contextlib import contextmanager

    from nice_weather.trading import recovery

    previous = checkpoint(runner)
    original = recovery.connect

    class FailUpdate:
        def __init__(self, con):
            self.con = con

        def execute(self, sql, args=()):
            if sql.startswith("UPDATE runs"):
                raise sqlite3.OperationalError("injected view failure")
            return self.con.execute(sql, args)

    @contextmanager
    def fail(path):
        with original(path) as con:
            yield FailUpdate(con)

    rows = [book(i, runner.session.now / 1e9 + i / 10) for i in range(1, 18)]
    with monkeypatch.context() as patch:
        patch.setattr(recovery, "connect", fail)
        with pytest.raises(sqlite3.OperationalError, match="view failure"):
            apply_feed_page(runner, rows)
    assert checkpoint(runner) == previous
    with connect(runner.results.path, readonly=True) as con:
        config = json.loads(con.execute("SELECT config FROM runs WHERE run_id='run'").fetchone()[0])
        assert config["feed_cursor"] == 0
    restarted = PaperRunner(runner.results, runner.results.run("run"))
    try:
        apply_feed_page(restarted, rows)
        assert checkpoint(restarted)["config"]["feed_cursor"] == 17
        assert restarted.session.snapshot()["cash"] == 100
        assert not restarted.session.snapshot()["fills"]
    finally:
        restarted.session.dispose()


def test_worker_flushes_page_before_heartbeat_and_user_order(tmp_path, monkeypatch):
    from test_knyc_terminal import scenario

    from nice_weather.trading import us_runtime
    from nice_weather.trading.feed import FeedStore
    from nice_weather.trading.storage import Requests

    now, contracts, _, _ = scenario()
    feed = FeedStore(tmp_path / "feed.sqlite3")
    feed.publish("contracts", "kalshi", contracts, now)
    for i in range(1, 4):
        row = book(i, now + i / 10)
        final_cursor = feed.publish("book", row["key"], row["data"], row["received"])
    account = "sandbox-kalshi-knyc"
    requests = Requests(tmp_path / "requests" / "requests.sqlite3")
    requests.submit("user-buy", account, "sandbox", "order", {
        "token": "test-1", "side": "BUY", "price": .5, "quantity": 2, "tif": "IOC"})
    checked = []
    original_apply, original_finish = PaperRunner.apply, Requests.finish

    def before_apply(item, input_id, event):
        with connect(item.results.path, readonly=True) as con:
            state = json.loads(con.execute("SELECT body FROM paper_state").fetchone()[0])
            assert state["config"]["feed_cursor"] == final_cursor
            assert state["quotes"]["test-1"]["best_bid"] == .3
            assert state["quotes"]["test-1:NO"]["best_ask"] == .7
        checked.append(event["kind"])
        return original_apply(item, input_id, event)

    def before_finish(item, input_id, *args, **kwargs):
        with connect(tmp_path / "results.sqlite3", readonly=True) as con:
            assert con.execute("SELECT 1 FROM paper_receipts WHERE input_id=?",
                               (input_id,)).fetchone()
        return original_finish(item, input_id, *args, **kwargs)

    monkeypatch.setattr(PaperRunner, "apply", before_apply)
    monkeypatch.setattr(Requests, "finish", before_finish)
    monkeypatch.setattr(us_runtime.time, "time_ns", lambda: int((now + 5) * 1e9))
    us_runtime.paper(tmp_path, "kalshi", once=True)
    assert checked == ["clock", "order"]
    run = Results(tmp_path / "results.sqlite3").run(account=account)
    assert len(json.loads(run["snapshot"])["fills"]) == 1
    assert requests.pending(account, "sandbox") == []

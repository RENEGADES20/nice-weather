"""Native account checkpoint, atomically committed with the public view and receipts.

Market books are deliberately absent. A restarted paper session cancels resting orders
before accepting a fresh book; it never re-simulates an already committed fill.
"""

from __future__ import annotations

import json
import time

from nice_weather.trading.storage import connect, digest, encoded


def native_state(session):
    from nautilus_trader.accounting.accounts.cash import CashAccount

    cache = session.engine.cache
    account = cache.account_for_venue(next(iter(session.engine.list_venues())))
    return {
        "config": session.config,
        "metadata": session.metadata,
        "now": session.now,
        "owner": session.owner,
        "outcomes": session.outcomes,
        "settlements": getattr(session, "settlements", []),
        "counts": session.counts,
        "enabled": session.enabled,
        "parameters": session.parameters,
        "rejections": session.rejections,
        "peak": session.equity_peak,
        "maximum_drawdown": session.maximum_drawdown,
        "account": CashAccount.to_dict(account) if account else None,
        "orders": [[type(e).to_dict(e) for e in o.events] for o in cache.orders()],
        "positions": [[type(e).to_dict(e) for e in p.events] for p in cache.positions()],
        "archived": [[type(e).to_dict(e) for e in p.events] for p in cache.position_snapshots()],
    }


def restore(state):
    from nautilus_trader.model import events
    from nautilus_trader.model.enums import OmsType
    from nautilus_trader.model.events import AccountState
    from nautilus_trader.model.instruments import BinaryOption
    from nautilus_trader.model.orders import OrderUnpacker
    from nautilus_trader.model.position import Position

    from nice_weather.trading.engine import VENUE, Session

    session = Session(state["config"])
    # Register instruments without running the simulator until orders are loaded.
    temporary = Session(state["config"])
    try:
        for row in {r["condition_id"]: r for r in state["metadata"].values()}.values():
            temporary.apply({"kind": "contract", "ts": state["now"], "data": row})
        session.metadata = state["metadata"]
        for token, instrument in temporary.instruments.items():
            instrument = BinaryOption.from_dict(BinaryOption.to_dict(instrument))
            session.instruments[token] = instrument
            session.engine.add_instrument(instrument)
    finally:
        temporary.dispose()
    cache = session.engine.cache
    for history in state["orders"]:
        order = OrderUnpacker.from_init(events.OrderInitialized.from_dict(history[0]))
        for row in history[1:]:
            order.apply(getattr(events, row["type"]).from_dict(row))
        cache.add_order(order)
    for key in ("archived", "positions"):
        for history in state[key]:
            first = events.OrderFilled.from_dict(history[0])
            position = Position(cache.instrument(first.instrument_id), first)
            for row in history[1:]:
                position.apply(events.OrderFilled.from_dict(row))
            if key == "archived":
                cache.snapshot_position(position)
            else:
                cache.add_position(position, OmsType.NETTING)
    session.owner, session.outcomes = state["owner"], state["outcomes"]
    session.settlements = state.get("settlements", [])
    for token, value in session.outcomes.items():
        session.settlement_prices[session.instruments[token].id] = float(value)
    session.counts, session.parameters = state["counts"], state["parameters"]
    session.rejections = state["rejections"]
    session.equity_peak, session.maximum_drawdown = state["peak"], state["maximum_drawdown"]
    # No book is installed, so native startup cannot create a fill.
    session.apply({"kind": "clock", "ts": state["now"], "data": {}})
    if state["account"]:
        account = cache.account_for_venue(VENUE)
        for row in state["account"]["events"]:
            account.apply(AccountState.from_dict(row))
        cache.update_account(account)
    session.enabled = state["enabled"]
    resting = len(session.open_orders())
    for token in session.instruments:
        session.cancel_token(token)
    session.apply({"kind": "clock", "ts": state["now"] + 1, "data": {}})
    if resting:
        session.rejections.append(
            {
                "request_id": "recovery",
                "reason": f"Restart cancelled {resting} resting orders; "
                "previous queue priority unavailable",
            }
        )
    return session


class PaperRunner:
    def __init__(self, results, run):
        from nice_weather.trading.engine import Session
        from nice_weather.trading.worker import Runner

        self.results, self.run_id = results, run["run_id"]
        with connect(results.path) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS paper_state (
                    run_id TEXT PRIMARY KEY, body TEXT NOT NULL, checksum TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_receipts (
                    run_id TEXT NOT NULL, input_id TEXT NOT NULL,
                    PRIMARY KEY(run_id,input_id));
                CREATE TABLE IF NOT EXISTS paper_equity (
                    run_id TEXT NOT NULL, sample_key TEXT NOT NULL, ts INTEGER NOT NULL,
                    equity REAL, realized REAL, fees REAL, PRIMARY KEY(run_id,sample_key));
            """)
            saved = con.execute(
                "SELECT * FROM paper_state WHERE run_id=?", (self.run_id,)
            ).fetchone()
            self.seen = {
                r[0]
                for r in con.execute(
                    "SELECT input_id FROM paper_receipts WHERE run_id=?", (self.run_id,)
                )
            }
        if saved:
            state = json.loads(saved["body"])
            if digest(state) != saved["checksum"]:
                raise ValueError("Paper checkpoint checksum mismatch; account paused")
            self.session = restore(state)
        else:
            config = json.loads(run["config"])
            if results.inputs(self.run_id):
                legacy = Runner(results, run)
                state = native_state(legacy.session)
                legacy.session.dispose()
                state["config"] = state["config"] | {"execution_version": 3}
                self.session = restore(state)
                self.seen.update(e["input_id"] for e in results.inputs(self.run_id))
            else:
                self.session = Session(config | {"execution_version": 3})
        config = self.session.config
        config["execution"] = "L2 depth consumption; no maker queue priority; native fact recovery"
        config["config_hash"] = digest({k: v for k, v in config.items() if k != "config_hash"})
        self.last_state_hash = None
        self.last_minute = None
        self.last_valid = None
        self.commit("startup")

    def commit(self, input_id=None):
        state = native_state(self.session)
        snapshot = self.session.snapshot()
        state_hash = digest(
            {
                k: state[k]
                for k in ("orders", "account", "outcomes", "rejections", "config", "enabled")
            }
        )
        minute = self.session.now // 60_000_000_000
        valid = snapshot["equity"] is not None
        important = input_id is not None or state_hash != self.last_state_hash
        sample = important or minute != self.last_minute or valid != self.last_valid
        with connect(self.results.path) as con:
            if sample:
                con.execute(
                    "INSERT OR REPLACE INTO paper_state VALUES (?,?,?)",
                    (self.run_id, encoded(state), digest(state)),
                )
            if input_id:
                con.execute(
                    "INSERT OR IGNORE INTO paper_receipts VALUES (?,?)", (self.run_id, input_id)
                )
                self.seen.add(input_id)
            if sample and self.session.now > 0:
                sample_key = f"event:{self.session.now}" if important else f"minute:{minute}"
                con.execute(
                    "INSERT OR IGNORE INTO paper_equity VALUES (?,?,?,?,?,?)",
                    (
                        self.run_id,
                        sample_key,
                        self.session.now,
                        snapshot["equity"],
                        snapshot["realized_pnl"],
                        snapshot["fees"],
                    ),
                )
            # UI heartbeat uses this one current row; no per-second append-only records.
            con.execute(
                "UPDATE runs SET snapshot=?,config=?,updated=?,status='running',error=NULL "
                "WHERE run_id=?",
                (encoded(snapshot), encoded(self.session.config), time.time(), self.run_id),
            )
        self.last_state_hash, self.last_minute, self.last_valid = state_hash, minute, valid
        return snapshot

    def apply(self, input_id, event):
        if input_id in self.seen:
            return self.session.snapshot()
        self.session.apply(event)
        # These observations never enter the durable receipt/input log.
        return self.commit(None if event["kind"] in {"clock", "depth"} else input_id)

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
        "strategy_state": session.strategy_state,
        "signals": session.signals,
        "latest_weather": session.latest_weather,
        "fee_accumulators": session.fee_accumulators,
        **({"market_prices": session.market_prices, "quotes": session.quotes,
            "execution_evidence": session.execution_evidence,
            "order_payloads": session.order_payloads} if session.approximate else {}),
        "rejections": session.rejections,
        "peak": session.equity_peak,
        "maximum_drawdown": session.maximum_drawdown,
        "account": CashAccount.to_dict(account) if account else None,
        "orders": [[type(e).to_dict(e) for e in o.events] for o in cache.orders()],
        "positions": [[type(e).to_dict(e) for e in p.events] for p in cache.positions()],
        "archived": [[type(e).to_dict(e) for e in p.events] for p in cache.position_snapshots()],
    }


def restore(state):
    from nautilus_trader.core.uuid import UUID4
    from nautilus_trader.model import events
    from nautilus_trader.model.enums import OmsType
    from nautilus_trader.model.events import AccountState
    from nautilus_trader.model.instruments import BinaryOption
    from nautilus_trader.model.orders import OrderUnpacker
    from nautilus_trader.model.position import Position

    from nice_weather.trading.engine import Session

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
    resting = 0
    for history in state["orders"]:
        order = OrderUnpacker.from_init(events.OrderInitialized.from_dict(history[0]))
        for row in history[1:]:
            order.apply(getattr(events, row["type"]).from_dict(row))
        if order.is_open:
            # Backtest startup re-submits cached open orders, even without an L2 book.
            # Record the restart cancellation before exposing them to that startup path.
            order.apply(
                events.OrderCanceled(
                    order.trader_id,
                    order.strategy_id,
                    order.instrument_id,
                    order.client_order_id,
                    order.venue_order_id,
                    order.account_id,
                    UUID4(),
                    state["now"],
                    state["now"],
                    True,
                )
            )
            resting += 1
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
    session.strategy_state = state.get("strategy_state", {})
    session.signals = state.get("signals", {})
    session.latest_weather = state.get("latest_weather")
    session.fee_accumulators = state.get("fee_accumulators", {})
    session.market_prices = state.get("market_prices", {})
    session.execution_evidence = state.get("execution_evidence", {})
    session.order_payloads = state.get("order_payloads", {})
    if session.approximate:
        session.quotes = state.get("quotes", {})
    session.rejections = state["rejections"]
    session.equity_peak, session.maximum_drawdown = state["peak"], state["maximum_drawdown"]
    # Only closed orders are loaded; native startup has no order to re-submit.
    session.apply({"kind": "clock", "ts": state["now"], "data": {}})
    if state["account"]:
        account = cache.account_for_venue(session.venue)
        for row in state["account"]["events"]:
            account.apply(AccountState.from_dict(row))
        if resting:
            last = state["account"]["events"][-1]
            # Release cancelled reservations without changing total cash.
            account.apply(
                AccountState.from_dict(
                    last
                    | {
                        "balances": [
                            b | {"free": b["total"], "locked": "0"} for b in last["balances"]
                        ],
                        "reported": True,
                        "event_id": str(UUID4()),
                        "ts_event": state["now"],
                        "ts_init": state["now"],
                        "info": {"reason": "Paper restart cancelled resting orders"},
                    }
                )
            )
        cache.update_account(account)
    session.enabled = state["enabled"]
    session.apply({"kind": "clock", "ts": state["now"] + 1, "data": {}})
    if state["account"]:
        expected = AccountState.from_dict(state["account"]["events"][-1])
        account = cache.account_for_venue(session.venue)
        for balance in expected.balances:
            if account.balance_total(balance.currency) != balance.total:
                session.dispose()
                raise ValueError("Recovery changed recorded cash; account paused")
    stored_fills = sum(e["type"] == "OrderFilled" for history in state["orders"] for e in history)
    if len(session.snapshot()["fills"]) != stored_fills:
        session.dispose()
        raise ValueError("Recovery changed recorded fills; account paused")
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
                CREATE TABLE IF NOT EXISTS paper_resets (
                    run_id TEXT NOT NULL, input_id TEXT NOT NULL, ts INTEGER NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(run_id,input_id));
            """)
            from nice_weather.trading.paper_execution import ensure_equity

            ensure_equity(con)
            saved = con.execute(
                "SELECT * FROM paper_state WHERE run_id=?", (self.run_id,)
            ).fetchone()
            last_sample = con.execute(
                "SELECT ts,equity FROM paper_equity WHERE run_id=? ORDER BY ts DESC LIMIT 1",
                (self.run_id,),
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
        self.last_signal_events = None
        snapshot = self.session.snapshot()
        self.last_financial_hash = (
            digest([snapshot["fills"], snapshot["settled"], config.get("funding_events", [])])
            if saved
            else None
        )
        self.last_minute = last_sample["ts"] // 60_000_000_000 if last_sample else None
        self.last_valid = last_sample["equity"] is not None if last_sample else None
        self.commit("startup")

    def commit(self, input_id=None, archive=None):
        state = native_state(self.session)
        snapshot = self.session.snapshot()
        state_hash = digest(
            {
                k: state[k]
                for k in (
                    "orders",
                    "account",
                    "outcomes",
                    "rejections",
                    "config",
                    "enabled",
                    "metadata",
                    "strategy_state",
                    "signals",
                    "latest_weather",
                    "fee_accumulators",
                )
            }
        )
        minute = self.session.now // 60_000_000_000
        valid = snapshot["equity"] is not None
        important = input_id is not None or state_hash != self.last_state_hash
        if self.session.approximate:
            # One bounded latest-price checkpoint, never an additional tick history.
            price_hash = digest(state["market_prices"])
            important |= price_hash != getattr(self, "last_price_hash", None)
            self.last_price_hash = price_hash
        sample = important or minute != self.last_minute or valid != self.last_valid
        financial_hash = digest(
            [snapshot["fills"], snapshot["settled"], self.session.config.get("funding_events", [])]
        )
        financial = financial_hash != self.last_financial_hash
        events = [e for signal in self.session.signals.values() for e in signal.get("events", [])]
        event_hash = digest(events)
        valuation = financial or minute != self.last_minute or valid != self.last_valid
        with connect(self.results.path) as con:
            if events and event_hash != self.last_signal_events:
                seq = con.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 FROM inputs WHERE run_id=?", (self.run_id,)
                ).fetchone()[0]
                con.execute(
                    "INSERT OR IGNORE INTO inputs VALUES (?,?,?,?,NULL)",
                    (self.run_id, seq, "signals-" + event_hash,
                     encoded({"kind": "signal-events", "events": events})),
                )
            if archive is not None:
                con.execute(
                    "INSERT INTO paper_resets VALUES (?,?,?,?)",
                    (self.run_id, input_id, self.session.now, encoded(archive)),
                )
            if sample:
                con.execute(
                    "INSERT INTO paper_state VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE "
                    "SET body=excluded.body,checksum=excluded.checksum",
                    (self.run_id, encoded(state), digest(state)),
                )
            if self.session.approximate and valuation:
                from nice_weather.trading.paper_execution import save_equity

                save_equity(con, self.run_id, snapshot)
            if input_id:
                con.execute(
                    "INSERT OR IGNORE INTO paper_receipts VALUES (?,?)", (self.run_id, input_id)
                )
                self.seen.add(input_id)
            if valuation and self.session.now > 0:
                sample_key = (
                    f"event:{self.session.now}"
                    if financial or valid != self.last_valid
                    else f"minute:{minute}"
                )
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
        self.last_financial_hash = financial_hash
        self.last_signal_events = event_hash
        return snapshot

    def apply(self, input_id, event):
        if input_id in self.seen:
            return self.session.snapshot()
        if event["kind"] in {"balance", "reset"}:
            return self.manage_account(input_id, event)
        self.session.apply(event)
        # These observations never enter the durable receipt/input log.
        return self.commit(None if event["kind"] in {"clock", "depth", "contract"} else input_id)

    def manage_account(self, input_id, event):
        from decimal import Decimal

        from nautilus_trader.accounting.accounts.cash import CashAccount
        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.model.events import AccountState

        from nice_weather.trading.engine import Session

        payload = event["data"]
        archive = None
        try:
            if self.session.config["mode"] != "sandbox":
                raise ValueError("Account controls are Paper only")
            value = Decimal(str(payload["cash"]))
            if not value.is_finite() or not 0 <= value <= 1_000_000 or value % Decimal("0.000001"):
                raise ValueError("Cash must be 0–1,000,000 pUSD, at most six decimal places")
            before = self.session.snapshot()
            if payload.get("expected_revision") != before["account_revision"]:
                raise ValueError("Account changed; review the current balance and retry")
            self.session.apply(event | {"kind": "clock"})
            if event["kind"] == "reset":
                if value <= 0:
                    raise ValueError("Reset starting cash must be positive")
                archive = native_state(self.session)
                config = self.session.config | {
                    "cash": float(value),
                    "started_ns": event["ts"],
                    "funding_events": [],
                    "strategy_id": "noop",
                    "parameters": {},
                    "tokens": [],
                }
                replacement = Session(config)
                for row in {r["condition_id"]: r for r in self.session.metadata.values()}.values():
                    replacement.apply({"kind": "contract", "ts": event["ts"], "data": row})
                replacement.apply({"kind": "clock", "ts": event["ts"], "data": {}})
                old = self.session
                self.session = replacement
                self.last_minute = None
                try:
                    result = self.commit(input_id, archive)
                except Exception:
                    self.session = old
                    replacement.dispose()
                    raise
                old.dispose()
                return result
            if float(value) < before["reserved"]:
                raise ValueError("Cancel open buy orders before reducing cash below reserved funds")
            account = self.session.engine.cache.account_for_venue(self.session.venue)
            last = CashAccount.to_dict(account)["events"][-1]
            delta = float(value) - before["cash"]
            balances = [
                b | {"total": str(value), "free": str(value - Decimal(b["locked"]))}
                for b in last["balances"]
            ]
            if any(Decimal(b["free"]) < 0 for b in balances):
                raise ValueError("Cash cannot be below native order reservations")
            account.apply(
                AccountState.from_dict(
                    last
                    | {
                        "balances": balances,
                        "event_id": str(UUID4()),
                        "reported": True,
                        "ts_event": event["ts"],
                        "ts_init": event["ts"],
                        "info": {"reason": "User Paper cash adjustment", "request_id": input_id},
                    }
                )
            )
            self.session.engine.cache.update_account(account)
            self.session.config.setdefault("funding_events", []).append(
                {"ts": event["ts"], "delta": delta, "request_id": input_id}
            )
            self.session.equity_peak += delta
        except (ValueError, KeyError, ArithmeticError) as exc:
            self.session.rejections.append(
                {"request_id": event["request_id"], "ts": event["ts"], "reason": str(exc)}
            )
        return self.commit(input_id)

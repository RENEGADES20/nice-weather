"""One US Live account per process. No deployment changes and no simulated fills."""

import argparse
import asyncio
import hashlib
import json
import time
from decimal import Decimal
from pathlib import Path

from nice_weather.trading.credentials import read_credentials
from nice_weather.trading.storage import Requests, Results, connect, encoded, single_writer
from nice_weather.trading.us_live_state import (
    availability,
    budget,
    configure,
    controls,
    install,
    money_facts,
    realized_today,
)
from nice_weather.trading.us_transport import USRest, amount, order_body


class LiveWorker:
    def __init__(self, root, transport, contracts):
        from nautilus_trader.cache.cache import Cache
        from nautilus_trader.common.component import LiveClock, MessageBus
        from nautilus_trader.config import LiveExecEngineConfig
        from nautilus_trader.live.execution_engine import LiveExecutionEngine
        from nautilus_trader.model.identifiers import TraderId
        from nautilus_trader.portfolio.portfolio import Portfolio

        from nice_weather.trading.us_execution import USExecutionClient

        self.root, self.transport = Path(root), transport
        self.account, self.venue = transport.account, transport.venue
        self.results = Results(self.root / "results.sqlite3")
        self.requests = Requests(self.root / "requests" / "requests.sqlite3")
        install(self.results.path)
        self.binding = hashlib.sha256((self.venue + ":" + transport.key_id).encode()).hexdigest()[
            :24
        ]
        self.clock, self.cache = LiveClock(), Cache()
        self.bus = MessageBus(TraderId("USLIVE-001"), self.clock)
        self.portfolio = Portfolio(msgbus=self.bus, cache=self.cache, clock=self.clock)
        self.engine = LiveExecutionEngine(
            loop=asyncio.get_running_loop(),
            msgbus=self.bus,
            cache=self.cache,
            clock=self.clock,
            config=LiveExecEngineConfig(
                generate_missing_orders=False, filter_unclaimed_external_orders=False
            ),
        )
        transport.gate = self.gate
        self.client = USExecutionClient(
            transport=transport,
            markets=[make_instrument(c) for c in contracts],
            loop=asyncio.get_running_loop(),
            msgbus=self.bus,
            cache=self.cache,
            clock=self.clock,
        )
        self.engine.register_client(self.client)
        self.client.refresh_execution = self.refresh
        self.snapshot = {
            "binding": self.binding,
            "authenticated": False,
            "reconciled": False,
            "funds": {},
            "orders": [],
            "fills": [],
            "positions": [],
            "received_at": 0,
        }
        self.changed = asyncio.Event()
        self.stream_connected = False
        self.stream_fault = None
        if not self.results.run(run_id=self.account):
            self.results.create(self.account, self.account, "live", {})

    def gate(self, account, contract, order):
        if account != self.account:
            raise ValueError("ACCOUNT_MISMATCH")
        self.snapshot["config"] = cfg = controls(self.results.path, self.account)
        action = "cancel" if order.get("kind") == "cancel" else "order"
        ready = availability(self.snapshot, action, order.get("owner", "manual"))
        if not ready["allowed"]:
            raise ValueError(ready["reason"])
        if action == "cancel":
            return True
        if contract["condition_id"] not in cfg["whitelist"]:
            raise ValueError("MARKET_NOT_WHITELISTED")
        if contract.get("parse_status") != "parsed":
            raise ValueError("MARKET_RULES_AMBIGUOUS")
        if not contract.get("active") or not contract.get("accepting_orders"):
            raise ValueError("MARKET_NOT_OPEN")
        order_body(self.venue, "validation", contract, order)
        q, p = Decimal(str(order["quantity"])), Decimal(str(order["price"]))
        from nice_weather.trading.us_fees import reserve

        fees = reserve(q, contract)
        cost = (p * q if order["side"] == "BUY" else Decimal(0)) + fees
        if cost > Decimal(cfg["order_limit"]):
            raise ValueError("ORDER_LIMIT")
        funds = self.snapshot["funds"]
        available = funds.get("available")
        if self.venue == "kalshi":
            shard = contract.get("exchange_index")
            available = funds.get("partitions", {}).get(str(shard))
            if available is None:
                raise ValueError("MARKET_SHARD_BALANCE_UNKNOWN")
        if available is None or cost > Decimal(available):
            raise ValueError(
                "INSUFFICIENT_SHARD_FUNDS"
                if self.venue == "kalshi"
                else "INSUFFICIENT_AVAILABLE_FUNDS"
            )
        market = contract["condition_id"]
        net = Decimal(
            next((r["net"] for r in self.snapshot["positions"] if r["market"] == market), "0")
        )
        if order["side"] == "SELL":
            held = max(Decimal(0), net if order["outcome"] == "YES" else -net)
            pending = sum(
                (
                    Decimal(r["remaining"])
                    for r in self.snapshot["orders"]
                    if r["market"] == market
                    and r["outcome"] == order["outcome"]
                    and r["side"] == "SELL"
                    and not r["terminal"]
                ),
                Decimal(0),
            )
            if q > held - pending:
                raise ValueError("INSUFFICIENT_POSITION")
        exposure = sum((Decimal(r["cost"]) for r in self.snapshot["positions"]), Decimal(0))
        reserve_cost = Decimal(budget(self.transport.path, self.venue)["reserved"])
        if order["side"] == "BUY" and exposure + reserve_cost + cost > Decimal(
            cfg["position_limit"]
        ):
            raise ValueError("POSITION_LIMIT")
        if Decimal(self.snapshot.get("daily_loss", "0")) >= Decimal(cfg["daily_loss_limit"]):
            raise ValueError("DAILY_LOSS_LIMIT")
        return True

    def publish(self):
        self.snapshot.update(
            config=controls(self.results.path, self.account),
            binding=self.binding,
            budget=budget(self.transport.path, self.venue),
            stream_connected=self.stream_connected,
        )
        self.snapshot["availability"] = {
            kind: availability(self.snapshot, kind)
            for kind in ("order", "cancel", "stop", "configure")
        }
        self.snapshot["market_rules"] = {
            c["condition_id"]: {
                k: c.get(k)
                for k in (
                    "parse_status",
                    "active",
                    "accepting_orders",
                    "fee_known",
                    "tick_size",
                    "quantity_step",
                    "minimum_order_size",
                    "exchange_index",
                )
            }
            for _, c in self.client.markets.values()
        }
        with connect(self.results.path) as con:
            con.execute(
                "UPDATE runs SET snapshot=?,updated=?,status=? WHERE run_id=?",
                (encoded(self.snapshot), time.time(), "running", self.account),
            )

    async def refresh(self):
        self.client.reconciled = False
        self.snapshot["reconciled"] = False
        try:
            await self.client.refresh_account()
            history = self.client.snapshot
            self.snapshot.update(
                authenticated=True,
                received_at=history["received_at"],
                funds=money_facts(self.venue, history["balance"]),
                external_positions=history["positions"],
                external_orders=history["orders"],
                activities=history.get("activities", []),
            )
            mass = await self.client.generate_mass_status(history=history)
            # Nautilus removes already-known reports in place; keep the full UI history.
            from nautilus_trader.core.uuid import UUID4
            from nautilus_trader.execution.reports import ExecutionMassStatus

            native_report = ExecutionMassStatus(
                client_id=mass.client_id,
                account_id=mass.account_id,
                venue=mass.venue,
                report_id=UUID4(),
                ts_init=mass.ts_init,
            )
            native_report.add_order_reports(list(mass.order_reports.values()))
            native_report.add_fill_reports(
                [r for group in mass.fill_reports.values() for r in group]
            )
            native_report.add_position_reports(
                [r for group in mass.position_reports.values() for r in group]
            )

            # Prevalidation refuses missing fills/positions; the engine cannot fabricate them.
            if not self.engine._reconcile_execution_mass_status(native_report):
                raise ValueError("NATIVE_RECONCILIATION_FAILED")
            from nice_weather.trading.us_reconcile import reconcile_budget

            reconcile_budget(self.client)
            self.project(mass)
            if self.venue == "poly_us" and not self.stream_connected:
                raise ValueError("PRIVATE_STREAM_DISCONNECTED")
            if self.stream_fault:
                raise ValueError(self.stream_fault)
            self.client.reconciled = True
            self.snapshot.update(reconciled=True, reconcile_reason=None)
        except Exception as exc:
            self.snapshot["authenticated"] = self.client.authenticated
            # Only known validation codes are exposed; no response bodies or credentials.
            self.snapshot["reconcile_reason"] = (
                str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            )
        self.publish()

    def project(self, mass):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from nautilus_trader.model.enums import OrderStatus

        terminal = {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        }
        orders = []
        for r in mass.order_reports.values():
            evidence = self.transport.attempt(r.client_order_id.value)["evidence"]
            orders.append(
                {
                    "id": r.client_order_id.value,
                    "venue_order_id": r.venue_order_id.value,
                    "market": self.client.markets[r.instrument_id][1]["condition_id"],
                    "owner": evidence["owner"],
                    "outcome": evidence["outcome"],
                    "side": evidence["side"],
                    "quantity": str(r.quantity),
                    "filled": str(r.filled_qty),
                    "remaining": str(r.quantity.as_decimal() - r.filled_qty.as_decimal())
                    if r.order_status not in terminal
                    else "0",
                    "price": str(
                        r.price.as_decimal()
                        if evidence["outcome"] == "YES"
                        else 1 - r.price.as_decimal()
                    ),
                    "status": r.order_status.name,
                    "terminal": r.order_status in terminal,
                }
            )
        fills = [
            {
                "order_id": r.venue_order_id.value,
                "trade_id": r.trade_id.value,
                "quantity": str(r.last_qty),
                "yes_price": str(r.last_px),
                "fee": str(r.commission.as_decimal()),
                "time_ns": r.ts_event,
            }
            for group in mass.fill_reports.values()
            for r in group
        ]
        positions = []
        today = datetime.now(ZoneInfo("America/New_York")).date()
        for p in self.cache.positions():
            if p.account_id != self.client.account_id:
                continue
            if p.is_closed:
                continue
            net = Decimal(str(p.signed_qty))
            px = Decimal(str(p.avg_px_open))
            positions.append(
                {
                    "market": self.client.markets[p.instrument_id][1]["condition_id"],
                    "net": str(net),
                    "cost": str(abs(net) * (px if net > 0 else 1 - px)),
                    "realized_pnl": str(p.realized_pnl.as_decimal()),
                }
            )
        pnl = realized_today([r for group in mass.fill_reports.values() for r in group], today)
        self.snapshot.update(
            orders=orders,
            fills=fills,
            positions=positions,
            daily_loss=str(max(Decimal(0), -pnl)),
            daily_realized_pnl=str(pnl),
            daily_loss_basis="纽约成交日已实现净损益，含费用；未实现风险由持仓成本限额约束",
        )

    async def command(self, request):

        from nautilus_trader.common.factories import OrderFactory
        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.execution.messages import SubmitOrder
        from nautilus_trader.model.enums import OrderSide, TimeInForce
        from nautilus_trader.model.identifiers import ClientOrderId, StrategyId

        kind, payload, rid = request["kind"], json.loads(request["payload"]), request["request_id"]
        if kind == "configure":
            configure(self.results.path, self.account, rid, payload, self.binding)
        elif kind in {"start", "stop"}:
            cfg = controls(self.results.path, self.account)
            selected = payload.get("strategies", ["S1", "S2", "S3"])
            if not selected or any(s not in cfg["strategies"] for s in selected):
                raise ValueError("INVALID_STRATEGY")
            patch = {
                "revision": payload.get("revision", cfg["revision"]),
                "strategies": cfg["strategies"] | {s: kind == "start" for s in selected},
            }
            # Stable command receipt allows a stopped-but-not-yet-cancelled request to resume.
            with connect(self.results.path) as con:
                prior = con.execute(
                    "SELECT body FROM us_live_control_receipts WHERE request_id=?", (rid,)
                ).fetchone()
            configure(
                self.results.path,
                self.account,
                rid,
                json.loads(prior[0]) if prior else patch,
                self.binding,
            )
            if kind == "stop":
                for order in self.snapshot["orders"]:
                    if order["owner"] in selected and not order["terminal"]:
                        await self.cancel("strategy-stop-" + order["owner"], order["id"])
        elif kind == "reconcile":
            await self.refresh()
        elif kind == "cancel":
            ids = payload.get("order_ids", [payload.get("order_id")])
            if not ids or any(not isinstance(i, str) or not i for i in ids):
                raise ValueError("ORDER_ID_REQUIRED")
            statuses = [await self.cancel(rid, identifier) for identifier in ids]
            return "unknown" if "unknown" in statuses else "accepted"
        elif kind in {"order", "close"}:
            self.snapshot["config"] = controls(self.results.path, self.account)
            market = payload["market"]
            ins, contract = next(
                (i, c) for i, c in self.client.markets.values() if c["condition_id"] == market
            )
            instruction = payload | {"owner": payload.get("owner", "manual")}
            if kind == "close":
                instruction["side"] = "SELL"
            if instruction["owner"] not in {"manual", "S1", "S2", "S3"}:
                raise ValueError("INVALID_OWNER")
            previous = self.transport.attempt(rid)
            if previous:
                return previous["status"]
            if self.venue == "kalshi":
                raw = (await self.transport.read("/markets/" + market))["market"]
                if raw.get("ticker") != market or type(raw.get("exchange_index")) is not int:
                    raise ValueError("MARKET_SHARD_UNKNOWN")
                contract = contract | {"exchange_index": raw["exchange_index"]}
                self.client.markets[ins.id] = (ins, contract)
            self.gate(self.account, contract, instruction)
            q = amount(instruction["quantity"], contract["quantity_step"])
            p = amount(instruction["price"], contract["tick_size"], upper=1)
            yes = instruction["outcome"] == "YES"
            buy = instruction["side"] == "BUY"
            factory = OrderFactory(
                self.bus.trader_id, StrategyId(instruction["owner"] + "-001"), self.clock
            )
            order = factory.limit(
                ins.id,
                OrderSide.BUY if buy == yes else OrderSide.SELL,
                ins.make_qty(q),
                ins.make_price(p if yes else 1 - p),
                time_in_force=TimeInForce[instruction.get("tif", "IOC")],
                client_order_id=ClientOrderId(rid),
            )
            self.cache.add_order(order)
            cmd = SubmitOrder(
                self.bus.trader_id,
                order.strategy_id,
                order,
                UUID4(),
                self.clock.timestamp_ns(),
                params={k: instruction[k] for k in ("outcome", "owner")}
                | {"action": instruction["side"]},
            )
            try:
                await self.client._submit_order(cmd)
            except Exception:
                # A failed follow-up GET cannot turn an accepted/unknown POST into a rejection.
                if self.transport.attempt(rid) is None:
                    raise
            await asyncio.sleep(0)
            attempt = self.transport.attempt(rid)
            if not attempt:
                raise ValueError("ORDER_NOT_SUBMITTED")
            await self.refresh()
            return attempt["status"]
        else:
            raise ValueError("UNSUPPORTED_LIVE_COMMAND")
        self.publish()
        return "accepted"

    async def cancel(self, request_id, order_id):
        from nautilus_trader.core.uuid import UUID4
        from nautilus_trader.execution.messages import CancelOrder
        from nautilus_trader.model.identifiers import ClientOrderId, StrategyId, VenueOrderId

        attempt = self.transport.attempt(order_id)
        if not attempt or not attempt.get("response"):
            raise ValueError("UNKNOWN_ORDER_IDENTITY")
        intent = attempt["evidence"]
        market = intent["body"].get("ticker", intent["body"].get("marketSlug"))
        ins, contract = next(
            (i, c) for i, c in self.client.markets.values() if c["condition_id"] == market
        )
        remote = attempt["response"]["order_id" if self.venue == "kalshi" else "id"]
        self.client.order_evidence(ClientOrderId(order_id), VenueOrderId(remote), contract)
        cancel_id = "cancel-" + hashlib.sha256((request_id + ":" + order_id).encode()).hexdigest()
        native_order = self.cache.order(ClientOrderId(order_id))
        result = await self.client._cancel_order(
            CancelOrder(
                trader_id=self.bus.trader_id,
                strategy_id=native_order.strategy_id if native_order else StrategyId("manual-001"),
                instrument_id=ins.id,
                client_order_id=ClientOrderId(order_id),
                venue_order_id=VenueOrderId(remote),
                command_id=UUID4(),
                ts_init=self.clock.timestamp_ns(),
                params={"request_id": cancel_id},
            )
        )
        if result["status"] == "rejected":
            raise ValueError("CANCEL_" + result["status"].upper())
        await self.refresh()
        return "accepted" if result["status"] == "accepted" else "unknown"

    async def tick(self):
        # Stopping survives disconnects even when cancellation was not yet possible.
        cfg = controls(self.results.path, self.account)
        for order in list(self.snapshot["orders"]):
            if (
                order["owner"] in cfg["strategies"]
                and not cfg["strategies"][order["owner"]]
                and not order["terminal"]
            ):
                try:
                    await self.cancel("strategy-stop-" + order["owner"], order["id"])
                    self.snapshot.pop("stop_reason", None)
                except Exception:
                    self.snapshot["stop_reason"] = "STRATEGY_STOPPED_CANCEL_PENDING"
        # Reconcile queue receipts whose original network result was uncertain.
        with connect(self.requests.path) as con:
            unknown = con.execute(
                "SELECT request_id,kind,payload FROM requests WHERE account=? AND mode='live' "
                "AND status='unknown'",
                (self.account,),
            ).fetchall()
        for row in unknown:
            attempt = self.transport.attempt(row[0])
            if row["kind"] == "cancel":
                payload = json.loads(row["payload"])
                ids = payload.get("order_ids", [payload.get("order_id")])
                receipts = [
                    self.transport.attempt(
                        "cancel-" + hashlib.sha256((row[0] + ":" + i).encode()).hexdigest()
                    )
                    for i in ids
                ]
                if all(r and r["status"] == "accepted" for r in receipts):
                    self.requests.finish(row[0], "accepted")
            if attempt and attempt["status"] in {"accepted", "rejected"}:
                self.requests.finish(row[0], attempt["status"], attempt.get("error"))
        for request in self.requests.pending(self.account, "live"):
            try:
                if request["expires"] < time.time() and not self.transport.attempt(
                    request["request_id"]
                ):
                    raise ValueError("REQUEST_EXPIRED")
                status = await self.command(request)
                self.requests.finish(request["request_id"], status)
            except Exception as exc:
                attempt = self.transport.attempt(request["request_id"])
                if attempt:
                    status = "unknown" if attempt["status"] == "submitting" else attempt["status"]
                else:
                    status = "rejected"
                message = (
                    str(exc) if isinstance(exc, (ValueError, KeyError)) else type(exc).__name__
                )
                self.requests.finish(request["request_id"], status, message)


def make_instrument(contract):
    from nautilus_trader.model.enums import AssetClass
    from nautilus_trader.model.identifiers import InstrumentId, Symbol
    from nautilus_trader.model.instruments import BinaryOption
    from nautilus_trader.model.objects import Currency, Price, Quantity

    tick, step = str(contract["tick_size"]), str(contract["quantity_step"])
    ins = BinaryOption(
        instrument_id=InstrumentId.from_str(
            contract["condition_id"] + "." + contract["venue"].upper()
        ),
        raw_symbol=Symbol(contract["condition_id"]),
        asset_class=AssetClass.ALTERNATIVE,
        currency=Currency.from_str("USD"),
        price_precision=max(0, -Decimal(tick).as_tuple().exponent),
        price_increment=Price.from_str(tick),
        size_precision=max(0, -Decimal(step).as_tuple().exponent),
        size_increment=Quantity.from_str(step),
        activation_ns=0,
        expiration_ns=4102444800000000000,
        ts_event=0,
        ts_init=time.time_ns(),
        outcome="YES",
    )
    return ins, contract


def collected_contracts(root, venue, precision):
    with connect(root / "feed.sqlite3", readonly=True) as con:
        has_watch = con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='settlement_watch'"
        ).fetchone()
        rows = con.execute(
            "SELECT contracts FROM settlement_watch WHERE venue=?"
            if has_watch
            else "SELECT body FROM feed_events WHERE kind='contracts' AND key=? ORDER BY seq",
            (venue,),
        ).fetchall()
    contracts = {c["condition_id"]: c for r in rows for c in json.loads(r[0])}
    if venue == "kalshi":
        for contract in contracts.values():
            contract.update(
                fee_rounding="kalshi_order_balance_v1",
                balance_precision=str(Decimal(1).scaleb(-precision)),
            )
    return contracts


async def private_stream(worker):
    from websockets.asyncio.client import connect as ws_connect

    poly = worker.venue == "poly_us"
    path = "/v1/ws/private" if poly else "/trade-api/ws/v2"
    host = "api.polymarket.us" if poly else "external-api-ws.kalshi.com"
    while True:
        try:
            async with ws_connect(
                "wss://" + host + path,
                additional_headers=worker.transport.headers("GET", path),
                ping_interval=15,
                close_timeout=3,
            ) as ws:
                if poly:
                    for name in ("ORDER", "ORDER_SNAPSHOT", "POSITION", "ACCOUNT_BALANCE"):
                        await ws.send(
                            encoded(
                                {
                                    "subscribe": {
                                        "requestId": "live-" + name,
                                        "subscriptionType": "SUBSCRIPTION_TYPE_" + name,
                                    }
                                }
                            )
                        )
                else:
                    await ws.send(
                        encoded(
                            {
                                "id": 1,
                                "cmd": "subscribe",
                                "params": {"channels": ["fill", "user_orders"]},
                            }
                        )
                    )
                worker.stream_connected = not poly
                worker.changed.set()
                async for message in ws:
                    row = json.loads(message, parse_float=str)
                    if "error" in row or row.get("type") == "error":
                        raise ValueError("PRIVATE_STREAM_ERROR")
                    if row.get("orderSubscriptionSnapshot", {}).get("eof") is True:
                        worker.stream_connected = True
                    execution = row.get("orderSubscriptionUpdate", {}).get("execution")
                    if execution:
                        try:
                            worker.transport.save_execution(execution)
                        except (ValueError, KeyError):
                            worker.stream_fault = "PRIVATE_FILL_EVIDENCE_INVALID"
                            raise
                    worker.changed.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            worker.stream_connected = False
            worker.client.reconciled = False
            worker.snapshot.update(reconciled=False, reconcile_reason="PRIVATE_STREAM_DISCONNECTED")
            worker.publish()
            await asyncio.sleep(2)


async def run(root, venue, credentials, precision):
    from nice_weather.trading.us_currency import register_live_usd

    # Kalshi fees are six-decimal amounts even when cash is aligned to 2/4 decimals.
    register_live_usd(venue, 6 if venue == "kalshi" else 2)
    key, secret = read_credentials(credentials, venue)
    transport = USRest(venue, "live-" + venue + "-knyc", key, secret, root / "results.sqlite3")
    contracts = collected_contracts(root, venue, precision)
    if not contracts:
        raise ValueError("NO_COLLECTED_MARKETS")
    worker = LiveWorker(root, transport, list(contracts.values()))
    worker.engine.start()
    stream = asyncio.create_task(private_stream(worker))
    try:
        while True:
            # New market days enter without restarting the worker; old positions stay registered.
            for contract in collected_contracts(root, venue, precision).values():
                ins, contract = make_instrument(contract)
                if ins.id not in worker.client.markets:
                    worker.client._instrument_provider.add(ins)
                    worker.cache.add_instrument(ins)
                worker.client.markets[ins.id] = (worker.cache.instrument(ins.id), contract)
            await worker.refresh()
            await worker.tick()
            try:
                await asyncio.wait_for(worker.changed.wait(), timeout=2)
            except TimeoutError:
                pass
            worker.changed.clear()
    finally:
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)
        worker.engine.stop()
        await transport.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--venue", choices=("kalshi", "poly_us"), required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--balance-precision", type=int, choices=(2, 4), required=True)
    args = parser.parse_args()
    with single_writer(args.root / ("live-" + args.venue + ".lock")):
        asyncio.run(run(args.root, args.venue, args.credentials, args.balance_precision))

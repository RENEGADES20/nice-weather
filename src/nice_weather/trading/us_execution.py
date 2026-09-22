"""US native account and order connections; reconciliation gates new risk."""

import asyncio
from decimal import Decimal

from nautilus_trader.accounting.factory import AccountFactory
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import GenerateOrderStatusReport
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import (
    AccountType,
    OmsType,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from nautilus_trader.model.identifiers import (
    AccountId,
    ClientId,
    ClientOrderId,
    Venue,
    VenueOrderId,
)
from nautilus_trader.model.objects import Currency

from nice_weather.trading.us_reports import account_state


class USAccountClient(LiveExecutionClient):
    def __init__(self, *, transport, loop, msgbus, cache, clock):
        venue = transport.venue
        if venue not in {"kalshi", "poly_us"}:
            raise ValueError("US transport required")
        prefix = "live-" + venue + "-"
        if not transport.account.startswith(prefix) or not transport.account[len(prefix):]:
            raise ValueError("Live account namespace required")
        account = AccountId(venue.upper() + "-" + transport.account[len(prefix):])
        super().__init__(
            loop=loop, client_id=ClientId(venue.upper()), venue=Venue(venue.upper()),
            oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
            base_currency=Currency.from_str("USD"), instrument_provider=InstrumentProvider(),
            msgbus=msgbus, cache=cache, clock=clock,
        )
        self._set_account_id(account)
        self.transport = transport
        self.snapshot = None
        self.authenticated = False
        self._refresh_lock = asyncio.Lock()

    async def refresh_account(self):
        # Serialize captures so a slower, older request cannot overwrite a newer one.
        async with self._refresh_lock:
            self.snapshot = None
            self.authenticated = False
            try:
                snapshot = await self.transport.account_history()
                if (snapshot["venue"] != self.transport.venue
                        or snapshot["account"] != self.transport.account):
                    raise ValueError("Account snapshot scope mismatch")
                self.authenticated = True
                state = account_state(
                    self.transport.venue, self.account_id, snapshot["balance"],
                    int(snapshot["received_at"] * 1_000_000_000),
                )
                previous = self._cache.account(self.account_id)
                candidate = previous if previous is not None else AccountFactory.create(state)
                if candidate.calculate_account_state:
                    raise RuntimeError("US Live accounts require a separate process from Paper")
                if previous is not None:
                    last = previous.last_event
                    if state.ts_init < last.ts_init or (
                        state.ts_event and last.ts_event and state.ts_event < last.ts_event
                    ):
                        raise ValueError("Stale account snapshot")
                self._send_account_state(state)
                native = self._cache.account(self.account_id)
                if native is None or native.last_event.id != state.id:
                    raise RuntimeError("Native portfolio did not apply account event")
                self.snapshot = snapshot
                return state
            except Exception:
                self.authenticated = False
                if hasattr(self, "reconciled"):
                    self.reconciled = False
                self._set_connected(False)
                raise

    async def _connect(self):
        await self.refresh_account()

    async def _disconnect(self):
        self.snapshot = None
        self.authenticated = False
        if hasattr(self, "reconciled"):
            self.reconciled = False
        await self.transport.close()

    async def _query_account(self, command):
        if command.account_id != self.account_id:
            raise ValueError("Account query scope mismatch")
        await self.refresh_account()

    async def generate_mass_status(self, lookback_mins=None):
        # Returning an empty report here would falsely reconcile real external orders.
        raise RuntimeError("US order/fill/position reconciliation is incomplete")


class USReadOnlyExecutionClient(USAccountClient):
    def __init__(self, *, transport, **kwargs):
        if transport.gate is not None:
            raise ValueError("Read-only US transport required")
        super().__init__(transport=transport, **kwargs)

    def _readonly(self, command):
        raise ValueError("Read-only US client cannot change orders")

    submit_order = _readonly
    submit_order_list = _readonly
    modify_order = _readonly
    cancel_order = _readonly
    cancel_all_orders = _readonly
    batch_cancel_orders = _readonly


class USExecutionClient(USAccountClient):
    """Native order transport. Account/risk activation belongs to the server gate.

    A registered market is a reconciliation scope, not permission to trade it.
    The transport gate and durable cumulative budget still run for every request.
    """

    def __init__(self, *, transport, markets, **kwargs):
        if not callable(transport.gate) or not markets:
            raise ValueError("Live risk gate and explicit market registry required")
        super().__init__(transport=transport, **kwargs)
        self.markets = {}
        self.reconciled = False
        self._command_lock = asyncio.Lock()
        for instrument, contract in markets:
            from nice_weather.trading.us_reports import scope

            scope(transport.venue, self.account_id, instrument,
                  contract["condition_id"], instrument.raw_symbol.value)
            if contract.get("station_id") != "KNYC" or instrument.id in self.markets:
                raise ValueError("Invalid or duplicate KNYC registry entry")
            self.markets[instrument.id] = (instrument, dict(contract))
            self._instrument_provider.add(instrument)
            self._cache.add_instrument(instrument)

    def order_instruction(self, command):
        order, params = command.order, command.params or {}
        _, contract = self.markets[order.instrument_id]
        outcome, action = params.get("outcome", "YES"), params.get("action")
        if action is None:
            action = "BUY" if order.side == OrderSide.BUY else "SELL"
        if outcome not in {"YES", "NO"} or action not in {"BUY", "SELL"}:
            raise ValueError("Invalid binary order intent")
        expected = OrderSide.BUY if (action == "BUY") == (outcome == "YES") else OrderSide.SELL
        if order.side != expected or order.order_type != OrderType.LIMIT:
            raise ValueError("Native order and binary intent disagree")
        if order.time_in_force not in {TimeInForce.GTC, TimeInForce.IOC, TimeInForce.FOK}:
            raise ValueError("Unsupported order validity")
        price = order.price.as_decimal()
        return contract, {"outcome": outcome, "side": action,
                          "price": str(price if outcome == "YES" else Decimal(1) - price),
                          "quantity": str(order.quantity.as_decimal()),
                          "tif": order.time_in_force.name,
                          "owner": params.get("owner", "manual")}

    async def _submit_order(self, command):
        order = command.order
        async with self._command_lock:
            previous = self.transport.attempt(order.client_order_id.value)
            if previous is not None:
                # Never reject or resubmit an identity which may already own exchange risk.
                self.reconciled = False
                return
            if not self.authenticated or not self.reconciled:
                self.generate_order_rejected(
                    order.strategy_id, order.instrument_id, order.client_order_id,
                    "ACCOUNT_RECONCILIATION_REQUIRED", self._clock.timestamp_ns())
                return
            try:
                contract, instruction = self.order_instruction(command)
                # A prior uncertain request keeps its original native state and is never resent.
                self.generate_order_submitted(order.strategy_id, order.instrument_id,
                                              order.client_order_id, self._clock.timestamp_ns())
                receipt = await self.transport.submit(order.client_order_id.value,
                                                      contract, instruction)
            except (ValueError, KeyError):
                if self.transport.attempt(order.client_order_id.value) is not None:
                    self.reconciled = False
                    return
                self.generate_order_rejected(order.strategy_id, order.instrument_id,
                                             order.client_order_id,
                                             "ORDER_VALIDATION_OR_RISK_REJECTED",
                                             self._clock.timestamp_ns())
                return
            if receipt["status"] == "rejected":
                self.generate_order_rejected(order.strategy_id, order.instrument_id,
                                             order.client_order_id, receipt["error"],
                                             self._clock.timestamp_ns())
            elif receipt["status"] == "accepted":
                field = "order_id" if self.transport.venue == "kalshi" else "id"
                venue_id = VenueOrderId(receipt["response"][field])
                self._cache.add_venue_order_id(order.client_order_id, venue_id)
                # HTTP receipt is not proof of a fill or of exchange acceptance.
                await self._report_order(order.instrument_id, order.client_order_id, venue_id)
            else:
                self.reconciled = False

    async def _report_order(self, instrument_id, client_order_id, venue_order_id):
        try:
            report = await self.generate_order_status_report(GenerateOrderStatusReport(
                instrument_id=instrument_id, client_order_id=client_order_id,
                venue_order_id=venue_order_id, command_id=UUID4(),
                ts_init=self._clock.timestamp_ns()))
            # A filled status without its FillReports makes Nautilus infer synthetic fills.
            # Publish order and execution facts together through validated mass reconciliation.
            return report
        except Exception:
            self.reconciled = False
            raise

    async def generate_order_status_report(self, command):
        from nice_weather.trading.us_reports import kalshi_order_report, poly_order_report

        instrument, contract = self.markets[command.instrument_id]
        venue_id = command.venue_order_id
        if venue_id is None and command.client_order_id is not None:
            venue_id = self._cache.venue_order_id(command.client_order_id)
        if venue_id is None:
            raise ValueError("Unknown venue order; reconciliation required")
        evidence = self.order_evidence(command.client_order_id, venue_id, contract)
        if self.transport.venue == "kalshi":
            payload = await self.transport.read("/portfolio/orders/" + venue_id.value)
            row = payload["order"]
            if row["order_id"] != venue_id.value:
                raise ValueError("Order response identity mismatch")
            if (command.client_order_id is not None
                    and row["client_order_id"] != command.client_order_id.value):
                raise ValueError("Client order identity mismatch")
            tif = {"good_till_canceled": TimeInForce.GTC,
                   "immediate_or_cancel": TimeInForce.IOC,
                   "fill_or_kill": TimeInForce.FOK}[evidence["body"]["time_in_force"]]
            return kalshi_order_report(self.account_id, instrument, contract["condition_id"], row,
                                       self._clock.timestamp_ns(), original_tif=tif,
                                       client_order_id=command.client_order_id)
        payload = await self.transport.read("/order/" + venue_id.value)
        if payload["order"]["id"] != venue_id.value:
            raise ValueError("Order response identity mismatch")
        return poly_order_report(self.account_id, instrument, contract["condition_id"],
                                 payload["order"], self._clock.timestamp_ns(),
                                 client_order_id=command.client_order_id)

    def order_evidence(self, client_order_id, venue_order_id, contract):
        """Bind a native identity to its durable request and exact venue receipt."""
        if client_order_id is None:
            raise ValueError("Client order identity required")
        attempt = self.transport.attempt(client_order_id.value)
        evidence = attempt and attempt.get("evidence")
        response = attempt and attempt.get("response")
        kalshi = self.transport.venue == "kalshi"
        if (not isinstance(evidence, dict) or not isinstance(response, dict)
                or response.get("order_id" if kalshi else "id") != venue_order_id.value
                or evidence.get("venue") != self.transport.venue
                or evidence.get("method") != "POST"
                or not isinstance(evidence.get("body"), dict)
                or evidence["body"].get("ticker" if kalshi else "marketSlug")
                != contract["condition_id"]):
            raise ValueError("Original order identity/evidence mismatch")
        return evidence

    async def generate_order_status_reports(self, command):
        """Report registered markets only; never infer closure from an absent order."""
        from nice_weather.trading.us_reports import kalshi_order_report, timestamp

        self.reconciled = False
        if command.instrument_id is not None and command.instrument_id not in self.markets:
            raise ValueError("Unregistered reconciliation instrument")
        snapshot = getattr(command, "history", None) or await self.transport.account_history()
        if (snapshot["account"] != self.transport.account
                or snapshot["venue"] != self.transport.venue):
            raise ValueError("Account history scope mismatch")
        received = int(snapshot["history_received_at"] * 1_000_000_000)
        kalshi = self.transport.venue == "kalshi"
        self.last_history = snapshot
        market_key, id_key = ("ticker", "order_id") if kalshi else ("marketSlug", "id")
        registry = {c["condition_id"]: (i, c) for i, c in self.markets.values()}
        rows = snapshot["orders"] if kalshi else snapshot["orders"]["orders"]
        if not isinstance(rows, list):
            raise ValueError("Invalid account order history")
        exchange = {}
        for row in rows:
            if row[market_key] in registry:
                if row[id_key] in exchange:
                    raise ValueError("Duplicate exchange order identity")
                exchange[row[id_key]] = row
        reports, owned = [], set()
        if kalshi and hasattr(self.transport, "recover_kalshi_identity"):
            for attempt in self.transport.attempts():
                self.transport.recover_kalshi_identity(attempt, rows)
        for attempt in self.transport.attempts():
            evidence = attempt.get("evidence")
            if not evidence:
                raise ValueError("Durable order evidence missing")
            # Cancellation attempts are separate commands, not additional orders.
            if evidence["path"] != (
                "/trade-api/v2/portfolio/events/orders" if kalshi else "/v1/orders"
            ):
                continue
            market = evidence["body"][market_key]
            if market not in registry:
                continue
            if attempt["status"] == "rejected":
                continue
            response = attempt.get("response")
            if not isinstance(response, dict) or not response.get(id_key):
                raise ValueError("Unknown submission requires exchange identity recovery")
            remote = VenueOrderId(response[id_key])
            if remote.value in owned:
                raise ValueError("Exchange order has multiple local owners")
            owned.add(remote.value)
            local = ClientOrderId(attempt["request_id"])
            instrument, contract = registry[market]
            original = self.order_evidence(local, remote, contract)
            if kalshi:
                row = exchange.get(remote.value)
                if row is None or row["client_order_id"] != local.value:
                    raise ValueError("Order missing or mismatched across history tiers")
                tif = {"good_till_canceled": TimeInForce.GTC,
                       "immediate_or_cancel": TimeInForce.IOC,
                       "fill_or_kill": TimeInForce.FOK}[original["body"]["time_in_force"]]
                report = kalshi_order_report(
                    self.account_id, instrument, market, row, received,
                    original_tif=tif, client_order_id=local)
            else:
                report = await self.generate_order_status_report(GenerateOrderStatusReport(
                    instrument_id=instrument.id, client_order_id=local, venue_order_id=remote,
                    command_id=UUID4(), ts_init=self._clock.timestamp_ns()))
            reports.append(report)
        self.external_orders = [r for key, r in exchange.items() if key not in owned]
        if any(r.get("status") == "resting" or r.get("state") in {
                "ORDER_STATE_NEW", "ORDER_STATE_PENDING_NEW", "ORDER_STATE_PENDING_RISK",
                "ORDER_STATE_PARTIALLY_FILLED", "ORDER_STATE_PENDING_CANCEL"}
               for r in self.external_orders):
            raise ValueError("Registered market has unattributed open orders")
        start = timestamp(command.start.isoformat(), received) if command.start else 0
        end = timestamp(command.end.isoformat(), received) if command.end else received
        if start > end:
            raise ValueError("Invalid report time range")
        return [r for r in reports
                if (command.instrument_id is None or r.instrument_id == command.instrument_id)
                and start <= r.ts_accepted <= end
                and (not command.open_only or r.order_status in {
                    OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.PENDING_CANCEL})]

    async def generate_mass_status(self, lookback_mins=None, *, history=None):
        from nice_weather.trading.us_reconcile import mass_status

        return await mass_status(self, history)

    async def generate_fill_reports(self, command):
        from nice_weather.trading.us_reports import timestamp

        mass = await self.generate_mass_status()
        received = mass.ts_init
        start = timestamp(command.start.isoformat(), received) if command.start else 0
        end = timestamp(command.end.isoformat(), received) if command.end else received
        if start > end:
            raise ValueError("Invalid report time range")
        return [r for reports in mass.fill_reports.values() for r in reports
                if (command.instrument_id is None or r.instrument_id == command.instrument_id)
                and start <= r.ts_event <= end
                and (command.venue_order_id is None or r.venue_order_id == command.venue_order_id)]

    async def generate_position_status_reports(self, command):
        mass = await self.generate_mass_status()
        return [r for reports in mass.position_reports.values() for r in reports
                if command.instrument_id is None or r.instrument_id == command.instrument_id]

    async def _cancel_order(self, command):
        async with self._command_lock:
            _, contract = self.markets[command.instrument_id]
            venue_id = command.venue_order_id or self._cache.venue_order_id(command.client_order_id)
            if venue_id is None:
                raise ValueError("Unknown venue order; cannot cancel by guess")
            self.order_evidence(command.client_order_id, venue_id, contract)
            receipt = await self.transport.cancel(
                (command.params or {}).get("request_id", "cancel-" + str(command.id)),
                contract, venue_id.value)
            if receipt["status"] == "rejected":
                self.generate_order_cancel_rejected(
                    command.strategy_id, command.instrument_id, command.client_order_id,
                    venue_id, receipt["error"], self._clock.timestamp_ns())
            else:
                # Cancellation is allowed while new risk is disabled; late fills are still reported.
                try:
                    await self._report_order(
                        command.instrument_id, command.client_order_id, venue_id)
                except Exception:
                    # REST follow-up failure cannot overwrite a durable cancel acknowledgement.
                    self.reconciled = False
            return receipt

    async def _submit_order_list(self, command):
        from nautilus_trader.execution.messages import SubmitOrder

        for order in command.order_list.orders:
            await self._submit_order(SubmitOrder(
                trader_id=command.trader_id, strategy_id=command.strategy_id, order=order,
                command_id=UUID4(), ts_init=self._clock.timestamp_ns(), params=command.params))
            # A list is never atomic. Each subsequent leg must pass refreshed account gates.
            await self.refresh_execution()
            if not self.reconciled:
                break

    async def _batch_cancel_orders(self, command):
        for cancel in command.cancels:
            await self._cancel_order(cancel)

    async def _cancel_all_orders(self, command):
        from nautilus_trader.execution.messages import CancelOrder

        for order in self._cache.orders_open(venue=self.venue, instrument_id=command.instrument_id):
            if order.strategy_id != command.strategy_id:
                continue
            await self._cancel_order(CancelOrder(
                trader_id=command.trader_id, strategy_id=order.strategy_id,
                instrument_id=order.instrument_id, client_order_id=order.client_order_id,
                venue_order_id=order.venue_order_id, command_id=UUID4(),
                ts_init=self._clock.timestamp_ns()))

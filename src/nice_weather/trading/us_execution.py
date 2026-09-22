"""US native read-only account connection; incomplete reports cannot imply reconciliation."""

import asyncio

from nautilus_trader.accounting.factory import AccountFactory
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.live.execution_client import LiveExecutionClient
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import AccountId, ClientId, Venue
from nautilus_trader.model.objects import Currency

from nice_weather.trading.us_reports import account_state


class USReadOnlyExecutionClient(LiveExecutionClient):
    def __init__(self, *, transport, loop, msgbus, cache, clock):
        venue = transport.venue
        if venue not in {"kalshi", "poly_us"} or transport.gate is not None:
            raise ValueError("Read-only US transport required")
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
                self._set_connected(False)
                raise

    async def _connect(self):
        await self.refresh_account()

    async def _disconnect(self):
        self.snapshot = None
        self.authenticated = False
        await self.transport.close()

    async def _query_account(self, command):
        if command.account_id != self.account_id:
            raise ValueError("Account query scope mismatch")
        await self.refresh_account()

    async def generate_mass_status(self, lookback_mins=None):
        # Returning an empty report here would falsely reconcile real external orders.
        raise RuntimeError("US order/fill/position reconciliation is incomplete")

    def _readonly(self, command):
        raise ValueError("Read-only US client cannot change orders")

    submit_order = _readonly
    submit_order_list = _readonly
    modify_order = _readonly
    cancel_order = _readonly
    cancel_all_orders = _readonly
    batch_cancel_orders = _readonly

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.portfolio.portfolio import Portfolio

from nice_weather.trading.us_execution import USReadOnlyExecutionClient


@pytest.mark.parametrize("venue", ["kalshi", "poly_us"])
def test_native_account_bus_cache_refresh_and_incomplete_reconciliation(venue):
    async def run():
        clock, cache = LiveClock(), Cache()
        bus = MessageBus(TraderId("TEST-001"), clock)
        portfolio = Portfolio(msgbus=bus, cache=cache, clock=clock)
        balance = ({"balance_dollars": "7.0001", "portfolio_value": 100, "updated_ts": 1}
                   if venue == "kalshi" else {"balances": [{"currency": "USD",
                       "currentBalance": "7.00", "buyingPower": "6.00"}]})
        snapshot = {"venue": venue, "account": "live-" + venue + "-test",
                    "balance": balance, "received_at": 1790056000, "reconciled": False}
        transport = SimpleNamespace(venue=venue, account=snapshot["account"], gate=None,
                                   account_history=AsyncMock(return_value=snapshot),
                                   close=AsyncMock())
        client = USReadOnlyExecutionClient(transport=transport, loop=asyncio.get_running_loop(),
                                          msgbus=bus, cache=cache, clock=clock)
        await client._connect()
        native = cache.account(client.account_id)
        assert native is not None
        assert native.balance_total() is None
        assert native.calculate_account_state is False
        first = native.last_event.id
        await client.refresh_account()
        assert native.last_event.id != first
        assert native.event_count == 2
        assert client.snapshot is snapshot
        assert portfolio.account(client.venue) is native
        transport.account_history.return_value = snapshot | {"received_at": 1790055999}
        with pytest.raises(ValueError, match="Stale"):
            await client.refresh_account()
        assert native.event_count == 2
        transport.account_history.return_value = snapshot
        with pytest.raises(RuntimeError, match="incomplete"):
            await client.generate_mass_status()
        for name in ("submit_order", "submit_order_list", "modify_order", "cancel_order",
                     "cancel_all_orders", "batch_cancel_orders"):
            with pytest.raises(ValueError, match="Read-only"):
                getattr(client, name)(None)
        transport.account_history.side_effect = OSError("interrupted capture")
        client._set_connected(True)
        with pytest.raises(OSError):
            await client.refresh_account()
        assert client.is_connected is False
        assert client.snapshot is None
        assert native.event_count == 2  # Failed refresh never resets existing facts to zero.
        await client._disconnect()
        transport.close.assert_awaited_once()
    asyncio.run(run())


def test_client_requires_readonly_transport():
    transport = SimpleNamespace(venue="kalshi", gate=lambda *args: True)
    with pytest.raises(ValueError, match="Read-only"):
        USReadOnlyExecutionClient(
            transport=transport, loop=None, msgbus=None, cache=None, clock=None,
        )

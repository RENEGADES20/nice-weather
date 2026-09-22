import asyncio
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.portfolio.portfolio import Portfolio

from nice_weather.trading.us_execution import USReadOnlyExecutionClient


def exercise_account(venue, contaminated=False):
    async def run():
        if contaminated:
            from nautilus_trader.accounting.factory import AccountFactory
            AccountFactory.register_calculated_account(venue.upper())
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
        if contaminated:
            with pytest.raises(RuntimeError, match="separate process"):
                await client._connect()
            assert cache.account(client.account_id) is None
            assert client.snapshot is None
            return
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


@pytest.mark.parametrize("venue,contaminated", [
    ("kalshi", False), ("poly_us", False), ("kalshi", True),
])
def test_native_account_bus_and_paper_process_isolation(venue, contaminated):
    # BacktestEngine mutates AccountFactory globally. Use the production process boundary.
    code = ("import runpy,sys; runpy.run_path(sys.argv[1])['exercise_account']"
            "(sys.argv[2], sys.argv[3]=='True')")
    result = subprocess.run([sys.executable, "-c", code, str(Path(__file__).resolve()),
                             venue, str(contaminated)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_client_requires_readonly_transport():
    transport = SimpleNamespace(venue="kalshi", gate=lambda *args: True)
    with pytest.raises(ValueError, match="Read-only"):
        USReadOnlyExecutionClient(
            transport=transport, loop=None, msgbus=None, cache=None, clock=None,
        )

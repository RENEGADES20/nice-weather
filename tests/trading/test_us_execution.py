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

from nice_weather.trading.us_execution import USExecutionClient, USReadOnlyExecutionClient


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
        assert client.authenticated is False
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


def test_existing_submission_is_never_rejected_or_resent():
    async def run():
        transport = SimpleNamespace(
            attempt=lambda request_id: {"status": "unknown"}, submit=AsyncMock(),
        )
        client = SimpleNamespace(
            transport=transport, _command_lock=asyncio.Lock(),
            authenticated=False, reconciled=True,
        )
        command = SimpleNamespace(order=SimpleNamespace(
            client_order_id=SimpleNamespace(value="existing-order")))
        # No rejection callback: emitting a rejection would fail this check.
        await USExecutionClient._submit_order(client, command)
        transport.submit.assert_not_awaited()
        assert client.reconciled is False
    asyncio.run(run())


@pytest.mark.parametrize("venue", ["kalshi", "poly_us"])
def test_order_evidence_binds_venue_receipt_and_market(venue):
    from nautilus_trader.model.identifiers import ClientOrderId, VenueOrderId

    kalshi = venue == "kalshi"
    evidence = {"venue": venue, "method": "POST",
                "body": {"ticker" if kalshi else "marketSlug": "KNYC-TEST"}}
    attempt = {"evidence": evidence,
               "response": {"order_id" if kalshi else "id": "exchange-order"}}
    client = SimpleNamespace(transport=SimpleNamespace(
        venue=venue, attempt=lambda request_id: attempt if request_id == "local-order" else None))
    local, remote = ClientOrderId("local-order"), VenueOrderId("exchange-order")
    contract = {"condition_id": "KNYC-TEST"}
    assert USExecutionClient.order_evidence(client, local, remote, contract) is evidence
    for candidate_local, candidate_remote, candidate_contract in (
        (ClientOrderId("other-order"), remote, contract),
        (local, VenueOrderId("other-exchange-order"), contract),
        (local, remote, {"condition_id": "OTHER-MARKET"}),
        (None, remote, contract),
    ):
        with pytest.raises(ValueError):
            USExecutionClient.order_evidence(
                client, candidate_local, candidate_remote, candidate_contract)


def test_native_bulk_orders_preserve_terminal_partial_and_fail_on_missing_history():
    import runpy

    from nautilus_trader.model.enums import OrderStatus
    from nautilus_trader.model.identifiers import AccountId

    fixtures = runpy.run_path(str(Path(__file__).with_name("test_us_reports.py")))
    ins = fixtures["instrument"]("kalshi")
    row = fixtures["fill"]() | {
        "client_order_id": "local-order", "type": "limit", "status": "canceled",
        "initial_count_fp": "2", "fill_count_fp": ".5", "remaining_count_fp": "0",
        "last_update_time": fixtures["STAMP"],
    }
    evidence = {"venue": "kalshi", "method": "POST",
                "path": "/trade-api/v2/portfolio/events/orders",
                "body": {"ticker": "KNYC-TEST", "time_in_force": "immediate_or_cancel"}}
    attempt = {"request_id": "local-order", "status": "accepted", "evidence": evidence,
               "response": {"order_id": "order-1"}}
    snapshot = {"account": "live-kalshi-test", "venue": "kalshi", "orders": [row],
                "history_received_at": fixtures["NOW"] / 1_000_000_000}

    async def run():
        transport = SimpleNamespace(
            account=snapshot["account"], venue="kalshi", attempts=lambda: [attempt],
            attempt=lambda request_id: attempt, account_history=AsyncMock(return_value=snapshot))
        client = SimpleNamespace(
            transport=transport, account_id=AccountId("KALSHI-test"), reconciled=True,
            markets={ins.id: (ins, {"condition_id": "KNYC-TEST"})})
        client.order_evidence = lambda *args: USExecutionClient.order_evidence(client, *args)
        command = SimpleNamespace(instrument_id=None, start=None, end=None, open_only=False)
        reports = await USExecutionClient.generate_order_status_reports(client, command)
        assert len(reports) == 1
        assert reports[0].order_status == OrderStatus.CANCELED
        assert str(reports[0].client_order_id) == "local-order"
        assert str(reports[0].filled_qty) == "0.5000"
        assert client.reconciled is False  # Order reports alone cannot reconcile fills/positions.
        snapshot["orders"] = []
        with pytest.raises(ValueError, match="missing"):
            await USExecutionClient.generate_order_status_reports(client, command)
        attempt["response"] = None
        attempt["status"] = "unknown"
        with pytest.raises(ValueError, match="Unknown submission"):
            await USExecutionClient.generate_order_status_reports(client, command)

    asyncio.run(run())

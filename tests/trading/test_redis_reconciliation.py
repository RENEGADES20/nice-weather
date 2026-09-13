"""Native Redis durability and official HTTP report reconciliation on loopback only."""

import asyncio
import shutil
import socket
import subprocess
import time
from unittest.mock import AsyncMock

import httpx
import msgspec
import pytest
from nautilus_trader.cache.cache import Cache
from nautilus_trader.cache.database import CacheDatabaseAdapter
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.config import CacheConfig, DatabaseConfig, LiveExecEngineConfig
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.messages import GenerateOrderStatusReport
from nautilus_trader.execution.reports import ExecutionMassStatus, PositionStatusReport
from nautilus_trader.live.execution_engine import LiveExecutionEngine
from nautilus_trader.model.enums import OmsType, PositionSide
from nautilus_trader.model.identifiers import AccountId, PositionId, TraderId, VenueOrderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.position import Position
from nautilus_trader.serialization.serializer import MsgSpecSerializer
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.events import TestEventStubs
from nautilus_trader.test_kit.stubs.execution import TestExecStubs
from py_clob_client_v2.http_helpers import helpers

from nice_weather.trading.live import execution_factory


@pytest.mark.parametrize("partial", [False, True])
def test_redis_restart_and_official_reconciliation(tmp_path, monkeypatch, partial):
    from nautilus_trader.adapters.polymarket.config import PolymarketExecClientConfig

    executable = shutil.which("redis-server")
    assert executable, "Redis acceptance requires redis-server; install it in the isolated runner"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    processes, databases = [], []

    def start_redis():
        process = subprocess.Popen(
            [
                executable,
                "--bind",
                "127.0.0.1",
                "--port",
                str(port),
                "--dir",
                str(tmp_path),
                "--appendonly",
                "yes",
                "--appendfsync",
                "always",
                "--save",
                "",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        processes.append(process)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            assert process.poll() is None, "Test Redis failed to start"
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1) as connection:
                    connection.sendall(b"*1\r\n$4\r\nPING\r\n")
                    if connection.recv(64) == b"+PONG\r\n":
                        return process
            except OSError:
                time.sleep(0.05)
        raise AssertionError("Test Redis did not become ready")

    config = CacheConfig(
        database=DatabaseConfig(type="redis", host="127.0.0.1", port=port),
        persist_account_events=True,
        flush_on_start=False,
        buffer_interval_ms=0,
    )
    trader = TraderId("TEST-001")

    def new_cache():
        database = CacheDatabaseAdapter(
            trader_id=trader,
            instance_id=UUID4(),
            serializer=MsgSpecSerializer(encoding=msgspec.msgpack, timestamps_as_str=True),
            config=config,
        )
        databases.append(database)
        return database, Cache(database=database, config=config)

    loop, clock = asyncio.new_event_loop(), LiveClock()
    client = engine = None
    try:
        server = start_redis()
        database, cache = new_cache()
        instrument = TestInstrumentProvider.binary_option()
        account_id = AccountId("POLYMARKET-001")
        account = TestExecStubs.cash_account(account_id=account_id)
        cache.add_account(account)
        cache.add_instrument(instrument)
        order = TestExecStubs.limit_order(
            instrument=instrument,
            trader_id=trader,
            quantity=instrument.make_qty(5),
            price=instrument.make_price(0.4),
        )
        cache.add_order(order)
        timestamp = 1789190000123456789
        order.apply(
            TestEventStubs.order_submitted(order, account_id=account_id, ts_event=timestamp)
        )
        cache.update_order(order)
        venue_id = VenueOrderId("unknown-submit-order")
        cache.add_venue_order_id(order.client_order_id, venue_id)
        cache.add("weather:expected-order:" + order.client_order_id.value, venue_id.value.encode())
        position = None
        if partial:
            order.apply(
                TestEventStubs.order_accepted(order, account_id=account_id, venue_order_id=venue_id)
            )
            cache.update_order(order)
            fill = TestEventStubs.order_filled(
                order,
                instrument,
                position_id=PositionId("acceptance-position"),
                last_qty=instrument.make_qty(2),
                last_px=instrument.make_price(0.4),
                commission=Money(0.02, instrument.quote_currency),
                ts_event=timestamp,
            )
            order.apply(fill)
            cache.update_order(order)
            position = Position(instrument, fill)
            cache.add_position(position, oms_type=OmsType.NETTING)
        # Native Redis writes are asynchronous. Verify the server has acknowledged
        # the event before testing durable restart; close() alone is not a barrier.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            persisted = database.load_order(order.client_order_id)
            if (
                persisted is not None
                and persisted.last_event.ts_event == timestamp
                and database.load().get("weather:expected-order:" + order.client_order_id.value)
                == venue_id.value.encode()
                and (not partial or database.load_position(position.id) is not None)
            ):
                break
            time.sleep(0.05)
        else:
            raise AssertionError("Native order event was not persisted")
        database.close()
        server.terminate()
        server.wait(timeout=10)

        start_redis()  # Same durable AOF, with a new Redis and native cache instance.
        database, restored = new_cache()
        bus = MessageBus(trader, clock)
        engine = LiveExecutionEngine(
            loop=loop,
            msgbus=bus,
            cache=restored,
            clock=clock,
            config=LiveExecEngineConfig(reconciliation=True),
        )
        engine.load_cache()  # TradingNode startup uses individual native loaders.
        recovered = restored.order(order.client_order_id)
        assert recovered.status.name == ("PARTIALLY_FILLED" if partial else "SUBMITTED")
        assert recovered.last_event.ts_event == timestamp
        assert restored.account(account_id).balances_total() == account.balances_total()
        if partial:
            assert restored.position(position.id).quantity == position.quantity
            assert restored.position(position.id).commissions() == position.commissions()
            assert recovered.trade_ids == order.trade_ids
        client = execution_factory().create(
            loop=loop,
            name="POLYMARKET",
            msgbus=bus,
            cache=restored,
            clock=clock,
            config=PolymarketExecClientConfig(
                private_key=format(1, "064x"),
                api_key="acceptance-only",
                api_secret="dGVzdA==",
                passphrase="acceptance-only",
                funder="0x" + "1" * 40,
                base_url_http="http://127.0.0.1:1",
                base_url_ws="ws://127.0.0.1:1",
            ),
        )
        assert restored.venue_order_id(order.client_order_id) == venue_id
        client._maintain_active_market = AsyncMock()
        condition, token = instrument.id.symbol.value.split("-")
        calls = []

        def transport(request):
            assert request.url.host == "127.0.0.1" and request.method == "GET"
            assert request.url.path == "/data/order/unknown-submit-order"
            calls.append(request.url.path)
            return httpx.Response(
                200,
                json=dict(
                    associate_trades=[],
                    id=venue_id.value,
                    status="CANCELED",
                    market=condition,
                    original_size="5",
                    outcome="Yes",
                    maker_address="0x" + "1" * 40,
                    owner="acceptance-only",
                    price="0.4",
                    side="BUY",
                    size_matched="2" if partial else "0",
                    asset_id=token,
                    expiration="0",
                    order_type="GTC",
                    created_at=1789190001,
                ),
            )

        command = GenerateOrderStatusReport(instrument.id, order.client_order_id, None, UUID4(), 0)
        with httpx.Client(transport=httpx.MockTransport(transport)) as http:
            monkeypatch.setattr(helpers, "_http_client", http)
            report = loop.run_until_complete(client.generate_order_status_report(command))
        assert calls and report is not None
        mass = ExecutionMassStatus(
            client.id, account_id, client.venue, UUID4(), clock.timestamp_ns()
        )
        mass.add_order_reports([report])
        if partial:
            mass.add_position_reports(
                [
                    PositionStatusReport(
                        account_id=account_id,
                        instrument_id=instrument.id,
                        position_side=PositionSide.LONG,
                        quantity=position.quantity,
                        report_id=UUID4(),
                        ts_last=timestamp,
                        ts_init=clock.timestamp_ns(),
                    )
                ]
            )
        # HTTP parsing is exercised above. Supply its native report at the startup
        # report boundary, then use the actual engine's startup reconciliation.
        client.generate_mass_status = AsyncMock(return_value=mass)
        engine.register_client(client)
        assert loop.run_until_complete(engine.reconcile_execution_state())
        assert recovered.status.name == "CANCELED"
        events = len(recovered.events)
        assert engine.reconcile_execution_report(report)
        assert len(recovered.events) == events
        assert recovered.trade_ids == order.trade_ids
        assert restored.account(account_id).balances_total() == account.balances_total()
    finally:
        if client:
            client._stop()
        if engine:
            engine.dispose()
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()
        for database in databases:
            database.close()
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

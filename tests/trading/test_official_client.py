"""Official adapter transport checks use generated test credentials and intercepted HTTP only."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from nautilus_trader.adapters.polymarket.config import PolymarketExecClientConfig
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.model.identifiers import TraderId
from py_clob_client_v2.exceptions import PolyApiException
from py_clob_client_v2.http_helpers import helpers

from nice_weather.trading.live import execution_factory


@pytest.fixture
def client_harness():
    """Real adapter and native order/cache objects; no remote endpoints are opened."""
    from nautilus_trader.test_kit.providers import TestInstrumentProvider
    from nautilus_trader.test_kit.stubs.events import TestEventStubs
    from nautilus_trader.test_kit.stubs.execution import TestExecStubs

    loop, clock, cache = asyncio.new_event_loop(), LiveClock(), Cache()
    bus = MessageBus(TraderId("TEST-001"), clock)
    events = []

    def process(event):
        events.append(event)
        order = cache.order(event.client_order_id)
        order.apply(event)
        cache.update_order(order)

    bus.register("ExecEngine.process", process)
    config = PolymarketExecClientConfig(
        private_key=format(1, "064x"),
        api_key="acceptance-only",
        api_secret="dGVzdA==",
        passphrase="acceptance-only",
        funder="0x" + "1" * 40,
        base_url_http="http://127.0.0.1:1",
        base_url_ws="ws://127.0.0.1:1",
        max_retries=1,
        retry_delay_initial_ms=1,
        retry_delay_max_ms=1,
    )
    client = execution_factory().create(
        loop=loop,
        name="POLYMARKET",
        config=config,
        msgbus=bus,
        cache=cache,
        clock=clock,
    )
    instrument = TestInstrumentProvider.binary_option()
    cache.add_instrument(instrument)
    order = TestExecStubs.limit_order(
        instrument=instrument,
        quantity=instrument.make_qty(5),
        price=instrument.make_price(0.4),
    )
    cache.add_order(order)
    order.apply(TestEventStubs.order_submitted(order, account_id=client.account_id))
    cache.update_order(order)
    client._update_account_state = AsyncMock()
    try:
        yield SimpleNamespace(
            client=client,
            cache=cache,
            clock=clock,
            loop=loop,
            bus=bus,
            order=order,
            instrument=instrument,
            events=events,
        )
    finally:
        client._stop()
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


@pytest.mark.parametrize("known_order_id", [True, False])
def test_signed_submit_timeout_remains_unknown(monkeypatch, client_harness, known_order_id):
    from py_clob_client_v2.clob_types import CreateOrderOptions, OrderArgsV2

    h = client_harness
    calls = []

    def transport(request):
        assert request.url.host == "127.0.0.1"
        assert request.method == "POST" and request.url.path == "/order"
        calls.append(json.loads(request.content))
        raise httpx.ReadTimeout("Controlled loss of submission response", request=request)

    token = h.instrument.id.symbol.value.split("-")[1]
    signed = h.client._http_client.builder.build_order(
        OrderArgsV2(token_id=token, price=0.4, size=5, side="BUY"),
        CreateOrderOptions(tick_size="0.001", neg_risk=False),
        version=2,
    )
    expected = h.client._expected_venue_order_id(signed, neg_risk=False)
    assert expected is not None
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        monkeypatch.setattr(helpers, "_http_client", http)
        h.loop.run_until_complete(
            h.client._post_signed_order(
                h.order,
                signed,
                expected_venue_order_id=expected if known_order_id else None,
            )
        )
    assert calls and all(body == calls[0] for body in calls)
    assert h.order.status.name == "SUBMITTED"
    assert not h.events  # No false reject/accept/fill when the response is unknown.
    assert h.cache.venue_order_id(h.order.client_order_id) == (expected if known_order_id else None)
    assert h.cache.get("weather:expected-order:" + h.order.client_order_id.value) == (
        expected.value.encode() if known_order_id else None
    )


def test_websocket_duplicate_fill_and_status_transitions(client_harness):
    from nautilus_trader.model.identifiers import VenueOrderId
    from nautilus_trader.test_kit.stubs.events import TestEventStubs

    h = client_harness
    venue_id = VenueOrderId("acceptance-order")
    h.order.apply(
        TestEventStubs.order_accepted(
            h.order,
            account_id=h.client.account_id,
            venue_order_id=venue_id,
        )
    )
    h.cache.update_order(h.order)
    h.cache.add_venue_order_id(h.order.client_order_id, venue_id)
    condition, token = h.instrument.id.symbol.value.split("-")
    message = dict(
        event_type="trade",
        asset_id=token,
        bucket_index=0,
        fee_rate_bps="0",
        id="acceptance-fill",
        last_update="1789190000",
        maker_address="0x" + "1" * 40,
        maker_orders=[],
        market=condition,
        match_time="1789190000",
        outcome="Yes",
        owner="acceptance-only",
        price="0.4",
        side="BUY",
        size="2",
        status="MATCHED",
        taker_order_id=venue_id.value,
        timestamp="1789190000000",
        trade_owner="acceptance-only",
        trader_side="TAKER",
        type="TRADE",
    )

    async def receive():
        for status in ("MATCHED", "MATCHED", "MINED", "CONFIRMED", "MATCHED"):
            h.client._handle_ws_message(json.dumps(message | {"status": status}).encode())
            await asyncio.sleep(0)  # Let the adapter's acknowledgement task deliver the fill.
        assert h.order.status.name == "PARTIALLY_FILLED"
        assert float(h.order.filled_qty) == 2
        assert len([e for e in h.events if type(e).__name__ == "OrderFilled"]) == 1
        # In-memory dedup is lost at restart; native persisted trade IDs still prevent replay.
        h.client._processed_fills.clear()
        h.client._processed_trades.clear()
        h.client._finalized_trades.clear()
        h.client._handle_ws_message(json.dumps(message).encode())
        await asyncio.sleep(0)
        assert float(h.order.filled_qty) == 2
        assert len(h.order.trade_ids) == 1

    h.loop.run_until_complete(receive())


@pytest.mark.parametrize("failure", ["auth", "timeout"])
def test_official_execution_client_transport_failure(monkeypatch, failure):
    called = []

    def transport(request):
        assert request.url.host == "127.0.0.1"
        called.append((request.method, request.url.path))
        if failure == "timeout":
            raise httpx.ReadTimeout("Acceptance timeout: remote result unknown")
        return httpx.Response(401, json={"error": "Invalid api key"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        monkeypatch.setattr(helpers, "_http_client", http)
        loop = asyncio.new_event_loop()
        clock = LiveClock()
        config = PolymarketExecClientConfig(
            private_key=format(1, "064x"),
            api_key="acceptance-only",
            api_secret="dGVzdA==",
            passphrase="acceptance-only",
            funder="0x" + "1" * 40,
            base_url_http="http://127.0.0.1:1",
            base_url_ws="ws://127.0.0.1:1",
        )
        client = execution_factory().create(
            loop=loop,
            name="POLYMARKET",
            config=config,
            msgbus=MessageBus(TraderId("TEST-001"), clock),
            cache=Cache(),
            clock=clock,
        )
        client._instrument_provider.initialize = AsyncMock()
        client._ws_client.disconnect = AsyncMock()
        try:
            with pytest.raises(PolyApiException):
                loop.run_until_complete(client._connect())
            assert called and all(method == "GET" for method, _ in called)
            assert not client.is_connected
        finally:
            client._stop()
            loop.close()

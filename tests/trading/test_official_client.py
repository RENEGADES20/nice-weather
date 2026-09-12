"""Official adapter transport checks use generated test credentials and intercepted HTTP only."""

import asyncio
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

"""Official adapter wiring. This release has no path which enables real orders."""

from __future__ import annotations


def status() -> dict:
    # Deliberately does not inspect environment secrets in dashboard/sandbox processes.
    return {
        "mode": "LIVE",
        "connected": False,
        "execution_enabled": False,
        "reason": "Real execution disabled for this delivery; no credentials loaded",
        "cash": None,
        "equity": None,
        "reconciled": False,
    }


def node_config(*, credentials: dict, instrument_ids: frozenset[str], redis_host="127.0.0.1"):
    from nautilus_trader.adapters.polymarket.config import (
        PolymarketDataClientConfig,
        PolymarketExecClientConfig,
    )
    from nautilus_trader.adapters.polymarket.providers import PolymarketInstrumentProviderConfig
    from nautilus_trader.config import (
        CacheConfig,
        DatabaseConfig,
        LiveExecEngineConfig,
        TradingNodeConfig,
    )

    required = {"private_key", "api_key", "api_secret", "passphrase", "funder"}
    if required - credentials.keys() or not all(credentials[k] for k in required):
        raise ValueError("Missing live credentials; opening disabled")
    if not instrument_ids or any(not i.endswith(".POLYMARKET") for i in instrument_ids):
        raise ValueError("Explicit validated KLGA instrument allowlist required")
    provider = PolymarketInstrumentProviderConfig(load_ids=instrument_ids)
    return TradingNodeConfig(
        trader_id="WEATHER-LIVE001",
        cache=CacheConfig(
            database=DatabaseConfig(type="redis", host=redis_host),
            persist_account_events=True,
            flush_on_start=False,
        ),
        exec_engine=LiveExecEngineConfig(reconciliation=True),
        data_clients={
            "POLYMARKET": PolymarketDataClientConfig(instrument_config=provider, **credentials)
        },
        exec_clients={
            "POLYMARKET": PolymarketExecClientConfig(instrument_config=provider, **credentials)
        },
    )


def build_node(config):
    """Construct native node/factories with risk halted; caller must not run real endpoints."""
    from nautilus_trader.adapters.polymarket.factories import (
        PolymarketLiveDataClientFactory,
    )
    from nautilus_trader.live.node import TradingNode
    from nautilus_trader.model.enums import TradingState

    node = TradingNode(config=config)
    node.add_data_client_factory("POLYMARKET", PolymarketLiveDataClientFactory)
    node.add_exec_client_factory("POLYMARKET", execution_factory())
    node.kernel.risk_engine.set_trading_state(TradingState.HALTED)
    return node


def execution_factory():
    from nautilus_trader.adapters.polymarket.factories import PolymarketLiveExecClientFactory
    from nautilus_trader.model.identifiers import VenueOrderId
    from py_clob_client_v2.exceptions import PolyApiException

    class GuardedPolymarketFactory(PolymarketLiveExecClientFactory):
        @staticmethod
        def create(**kwargs):
            client = PolymarketLiveExecClientFactory.create(**kwargs)
            cache = kwargs["cache"]
            prefix = "weather:expected-order:"
            for order in cache.orders():
                saved = cache.get(prefix + order.client_order_id.value)
                if saved:
                    cache.add_venue_order_id(order.client_order_id, VenueOrderId(saved.decode()))
            original_connect = client._connect
            original_post = client._post_signed_order

            async def post(order, signed_order, **options):
                expected = options.get("expected_venue_order_id")
                if expected is not None:
                    # Persist only the signed order ID, never credentials or the signed body.
                    # Native Cache remains the order/event source of truth.
                    cache.add(prefix + order.client_order_id.value, expected.value.encode())
                await original_post(order, signed_order, **options)

            async def connect():
                try:
                    await original_connect()
                except TypeError as exc:
                    # v1.231.0 indexes error_msg['error']; transport errors can carry a string.
                    cause = exc.__context__
                    if isinstance(cause, PolyApiException) and isinstance(cause.error_msg, str):
                        raise cause from exc
                    raise

            client._connect = connect
            client._post_signed_order = post
            return client

    return GuardedPolymarketFactory

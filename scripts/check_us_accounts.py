"""Explicit read-only authentication probe. Only GET requests; no enable or order calls."""

import argparse
import asyncio
import json
from pathlib import Path

from nice_weather.trading.credentials import read_credentials
from nice_weather.trading.us_transport import USRest


async def check(directory, output):
    output.mkdir(parents=True, exist_ok=True)
    summary = {}
    for venue, filename in (("kalshi", "KALSHI.txt"), ("poly_us", "POLY.txt")):
        client = None
        native_client = None
        authenticated = False
        try:
            key_id, secret = read_credentials(directory / filename, venue)
            client = USRest(
                venue, "live-" + venue + "-knyc", key_id, secret, output / "attempts.sqlite3"
            )
            from nautilus_trader.cache.cache import Cache
            from nautilus_trader.common.component import LiveClock, MessageBus
            from nautilus_trader.model.events import AccountState
            from nautilus_trader.model.identifiers import TraderId
            from nautilus_trader.portfolio.portfolio import Portfolio

            from nice_weather.trading.us_execution import USReadOnlyExecutionClient

            clock, cache = LiveClock(), Cache()
            bus = MessageBus(TraderId("READONLY-001"), clock)
            portfolio = Portfolio(msgbus=bus, cache=cache, clock=clock)
            native_client = USReadOnlyExecutionClient(
                transport=client, loop=asyncio.get_running_loop(),
                msgbus=bus, cache=cache, clock=clock,
            )
            state = await native_client.refresh_account()
            authenticated = True
            snapshot = native_client.snapshot
            native = cache.account(native_client.account_id)
            assert portfolio.account(native_client.venue) is native
            snapshot["native_account_state"] = AccountState.to_dict(state)
            (output / (venue + ".json")).write_text(json.dumps(snapshot), encoding="utf-8")
            summary[venue] = {
                "authenticated": True,
                "reconciled": False,
                "balance_fields": sorted(snapshot["balance"]),
                "orders_count": len(snapshot["orders"].get("orders", []))
                if isinstance(snapshot["orders"], dict)
                else len(snapshot["orders"]),
                "positions_count": len(snapshot["positions"]),
                "native_account_constructed": True,
                "native_portfolio_cache_applied": True,
                "native_cash_known": native.balance_total() is not None,
                "balance_mapping_status": state.info["balance_mapping_status"],
            }
        except Exception as exc:
            # Third-party exceptions may contain request data; emit only a controlled class.
            if native_client is not None:
                authenticated = native_client.authenticated
            summary[venue] = {"authenticated": authenticated, "reconciled": False,
                              "error_type": type(exc).__name__}
        finally:
            if client:
                await client.close()
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(check(args.credentials, args.output))

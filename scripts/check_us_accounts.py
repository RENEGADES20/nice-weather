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
        try:
            key_id, secret = read_credentials(directory / filename, venue)
            client = USRest(
                venue, "live-" + venue + "-knyc", key_id, secret, output / "attempts.sqlite3"
            )
            snapshot = await client.account_history()
            (output / (venue + ".json")).write_text(json.dumps(snapshot), encoding="utf-8")
            summary[venue] = {
                "authenticated": True,
                "reconciled": False,
                "balance_fields": sorted(snapshot["balance"]),
                "orders_count": len(snapshot["orders"].get("orders", []))
                if isinstance(snapshot["orders"], dict)
                else len(snapshot["orders"]),
                "positions_count": len(snapshot["positions"]),
            }
        except Exception as exc:
            # Third-party exceptions may contain request data; emit only a controlled class.
            summary[venue] = {"authenticated": False, "error_type": type(exc).__name__}
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

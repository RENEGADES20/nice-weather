"""Explicit real GET/WS verification. Writes are blocked at HTTP dispatch."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from nice_weather.trading.credentials import read_credentials
from nice_weather.trading.storage import connect
from nice_weather.trading.us_currency import register_live_usd
from nice_weather.trading.us_live import LiveWorker, private_stream
from nice_weather.trading.us_transport import USRest


async def verify(args):
    register_live_usd(args.venue, 6 if args.venue == "kalshi" else 2)
    key, secret = read_credentials(args.credentials, args.venue)
    args.output.mkdir(parents=True, exist_ok=False)
    transport = USRest(
        args.venue, "live-" + args.venue + "-knyc", key, secret, args.output / "results.sqlite3"
    )
    counts = {"read_requests": 0, "write_attempts": 0}

    async def read_only(request):
        if request.method != "GET":
            counts["write_attempts"] += 1
            raise RuntimeError("Real write forbidden in verification")
        counts["read_requests"] += 1

    transport.client.event_hooks["request"] = [read_only]
    with connect(args.feed, readonly=True) as con:
        rows = con.execute(
            "SELECT body FROM feed_events WHERE kind='contracts' AND key=? ORDER BY seq",
            (args.venue,),
        ).fetchall()
    contracts = {c["condition_id"]: c for row in rows for c in json.loads(row[0])}
    worker = LiveWorker(args.output, transport, list(contracts.values()))
    transport.gate = None
    stream = asyncio.create_task(private_stream(worker))
    try:
        await worker.refresh()
        await asyncio.sleep(3)
        await worker.refresh()
        snapshot = worker.snapshot
        summary = {
            "venue": args.venue,
            "verified_at": time.time(),
            **counts,
            "authenticated": snapshot["authenticated"],
            "reconciled": snapshot["reconciled"],
            "reason": snapshot.get("reconcile_reason"),
            "stream_connected": worker.stream_connected,
            "funds": snapshot["funds"],
            "managed_orders": len(snapshot["orders"]),
            "managed_fills": len(snapshot["fills"]),
            "managed_positions": len(snapshot["positions"]),
            "raw_orders": len(snapshot.get("external_orders", {}).get("orders", []))
            if args.venue == "poly_us"
            else len(snapshot.get("external_orders", [])),
            "raw_positions": len(snapshot.get("external_positions", [])),
            "source_hashes": {
                name: hashlib.sha256(
                    Path("src/nice_weather/trading", name).read_bytes()
                ).hexdigest()
                for name in ("us_live.py", "us_execution.py", "us_transport.py", "us_reconcile.py")
            },
        }
        (args.output / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        assert counts["write_attempts"] == 0
        assert summary["authenticated"], summary["reason"]
    finally:
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)
        await transport.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--venue", choices=("kalshi", "poly_us"), required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--feed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(verify(parser.parse_args()))

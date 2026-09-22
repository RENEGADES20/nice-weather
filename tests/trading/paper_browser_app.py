"""Local UI acceptance: labelled sample markets, real Paper worker/API/Nautilus ledger."""

import argparse
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from test_knyc_terminal import scenario

from nice_weather.trading.api import create_app
from nice_weather.trading.feed import FeedStore


def seed(root):
    store = FeedStore(root / "feed.sqlite3")
    now = time.time()
    _, rows, _, _ = scenario()
    for venue in ("kalshi", "poly_us"):
        contracts = [
            row
            | {
                "venue": venue,
                "condition_id": f"{venue}-sample-{i}",
                "yes_token_id": f"{venue}-sample-{i}",
                "no_token_id": f"{venue}-sample-{i}:NO",
                "label": f"开发样例 {i}",
                "fee_known": False,
                "local_day": datetime.now(UTC).date().isoformat(),
                "received_at": now,
                "observation_end": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            }
            for i, row in enumerate(rows)
        ]
        store.publish("contracts", venue, contracts, now)
        for row in contracts:
            store.publish(
                "book",
                row["yes_token_id"],
                {
                    "bids": [[0.3, 1]],
                    "asks": [[0.4, 1]],
                    "complete": True,
                    "received_at": now,
                    "source": "labelled_development_sample",
                },
                now,
            )


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    seed(args.root)
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "nice_weather.trading.us_runtime",
                "--root",
                str(args.root),
                "--venue",
                venue,
            ],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        for venue in ("kalshi", "poly_us")
    ]
    try:
        uvicorn.run(
            create_app(args.root, password="local-paper-test", origin="http://127.0.0.1:5175"),
            host="127.0.0.1",
            port=8767,
        )
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=10)

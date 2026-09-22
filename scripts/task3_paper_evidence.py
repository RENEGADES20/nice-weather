"""Read-only real-price evidence and a small exact-row replay slice; no production writes."""

import argparse
import hashlib
import json
from pathlib import Path

from nice_weather.trading.engine import Session
from nice_weather.trading.feed import FeedStore
from nice_weather.trading.paper_execution import VERSION
from nice_weather.trading.storage import connect
from nice_weather.trading.us_runtime import feed_event, replay
from nice_weather.trading.worker import run_config


def inspect(source, output):
    report = {"source": str(source), "source_access": "read_only", "venues": {}}
    for venue in ("kalshi", "poly_us"):
        with connect(source, readonly=True) as con:
            contract_row = dict(
                con.execute(
                    "SELECT * FROM feed_events WHERE kind='contracts' AND key=? "
                    "ORDER BY seq DESC LIMIT 1",
                    (venue,),
                ).fetchone()
            )
            contracts = json.loads(contract_row["body"])
            token = contracts[0]["yes_token_id"]
            rows = [
                dict(r)
                for r in con.execute(
                    "SELECT * FROM feed_events WHERE kind='book' AND key=? AND received>=? "
                    "ORDER BY seq LIMIT 20",
                    (token, contract_row["received"]),
                ).fetchall()
            ]
        session = Session(
            run_config("backtest-" + venue, "backtest", "S3")
            | {
                "venue": venue,
                "station_id": "KNYC",
                "execution_version": 3,
                "execution_model": VERSION,
                "projection_version": 2,
            }
        )
        try:
            for event in [contract_row, *rows]:
                for native in feed_event(session, event | {"data": json.loads(event["body"])}):
                    session.apply(native)
            selected = session.approximate_price(token, "BUY")
            snapshot = session.apply(
                {
                    "kind": "order",
                    "ts": session.now + 1,
                    "request_id": "real-sample",
                    "data": {
                        "token": token,
                        "side": "BUY",
                        "quantity": 1,
                        "price": 0.99,
                        "tif": "IOC",
                    },
                }
            )
            section = {
                "token": token,
                "day": contracts[0]["local_day"],
                "contract_seq": contract_row["seq"],
                "book_count": len(rows),
                "price": selected,
                "fills": len(snapshot["fills"]),
                "rejections": snapshot["rejections"],
                "rows_sha256": hashlib.sha256(
                    json.dumps([contract_row, *rows], sort_keys=True).encode()
                ).hexdigest(),
            }
        finally:
            session.dispose()
        root = output / venue
        FeedStore(root / "feed.sqlite3")
        with connect(root / "feed.sqlite3") as con:
            con.executemany(
                "INSERT OR IGNORE INTO feed_events VALUES (?,?,?,?,?)",
                [
                    (r["seq"], r["kind"], r["key"], r["received"], r["body"])
                    for r in [contract_row, *rows]
                ],
            )
        result = replay(
            root,
            venue,
            contract_row["received"] - 0.001,
            rows[-1]["received"] + 0.001,
            "S3",
            "real-evidence-" + venue,
        )
        with connect(root / "results.sqlite3", readonly=True) as con:
            section["equity_points"] = con.execute(
                "SELECT COUNT(*) FROM simulation_equity"
            ).fetchone()[0]
        section["replay_status"] = result["status"]
        section["replay_audit"] = json.loads(result["snapshot"])["replay_audit"]
        report["venues"][venue] = section
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect(args.source, args.output), ensure_ascii=False, indent=2))

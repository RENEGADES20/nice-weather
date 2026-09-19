"""US venue paper worker using native Nautilus facts and existing recovery."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from nice_weather.trading.feed import FeedStore
from nice_weather.trading.recovery import PaperRunner
from nice_weather.trading.storage import Requests, Results, digest, single_writer
from nice_weather.trading.us_markets import VENUES
from nice_weather.trading.worker import run_config


def feed_event(session, event):
    """One received-time conversion for both replay and online native sessions."""
    ts = max(session.now + 1, int(event["received"] * 1e9))
    if event["kind"] == "contracts":
        if event["key"] != session.config["venue"]:
            return []
        return [{"kind": "contract", "ts": ts + i, "data": c} for i, c in enumerate(event["data"])]
    if event["kind"] == "book" and event["key"] in session.metadata:
        book, token = event["data"], event["key"]
        received_ns = int(book["received_at"] * 1e9)
        events = []
        for token_id, bids, asks in (
            (token, book["bids"], book["asks"]),
            (
                token + ":NO",
                [[round(1 - p, 8), q] for p, q in book["asks"]],
                [[round(1 - p, 8), q] for p, q in book["bids"]],
            ),
        ):
            # Complement is a venue binary book identity, not an inferred weather probability.
            events.append(
                {
                    "kind": "depth",
                    "ts": ts + len(events),
                    "data": {
                        "token_id": token_id,
                        "bids": sorted(bids, reverse=True),
                        "asks": sorted(asks),
                        "received_ns": received_ns,
                        "valid": book["complete"],
                    },
                }
            )
        return events
    if event["kind"] == "prediction" and event["key"] == session.config["venue"]:
        return [{"kind": "weather_signal", "ts": ts, "data": event["data"]}]
    return []


def paper(root, venue, once=False):
    account = f"sandbox-{venue}-knyc"
    results = Results(root / "results.sqlite3")
    requests = Requests(root / "requests" / "requests.sqlite3")
    feed = FeedStore(root / "feed.sqlite3")
    with single_writer(root / (account + ".lock")):
        run = results.run(account=account)
        if not run:
            config = run_config(account, "sandbox", "S1_S2_S3") | {
                "venue": venue,
                "station_id": "KNYC",
                "execution_version": 3,
                "projection_version": 2,
                "feed_cursor": 0,
                "execution": "Native L2 IOC/GTC; received-time snapshots; no maker rebates",
            }
            config.pop("config_hash", None)
            config["config_hash"] = digest(config)
            results.create(account, account, "sandbox", config)
            run = results.run(account=account)
        runner = PaperRunner(results, run)
        try:
            while True:
                cursor = runner.session.config.get("feed_cursor", 0)
                events = feed.since(cursor)
                for event in events:
                    for native in feed_event(runner.session, event):
                        runner.session.apply(native)
                    runner.session.config["feed_cursor"] = event["seq"]
                    runner.commit(f"feed-{event['seq']}" if event["kind"] == "prediction" else None)
                now = max(time.time_ns(), runner.session.now + 1)
                runner.apply("clock", {"kind": "clock", "ts": now, "data": {}})
                for request in requests.pending(account, "sandbox"):
                    if request["expires"] < time.time():
                        requests.finish(request["request_id"], "rejected", "Request expired")
                        continue
                    payload = json.loads(request["payload"])
                    before = len(runner.session.rejections)
                    snapshot = runner.apply(
                        request["request_id"],
                        {
                            "kind": request["kind"],
                            "data": payload,
                            "ts": max(time.time_ns(), runner.session.now + 1),
                            "request_id": request["request_id"],
                        },
                    )
                    rejected = len(snapshot["rejections"]) > before
                    requests.finish(
                        request["request_id"],
                        "rejected" if rejected else "accepted",
                        snapshot["rejections"][-1]["reason"] if rejected else None,
                    )
                runner.commit()
                if once:
                    return
                time.sleep(0.05 if events else 0.2)
        except BaseException as exc:
            results.status(runner.run_id, "paused", type(exc).__name__)
            raise
        finally:
            runner.session.dispose()


def replay(root, venue, start, end, strategy="S1_S2_S3", request_id=None):
    """Replay original receipts, not final labels or reconstructed receipt times."""
    from nice_weather.trading.engine import Session
    from nice_weather.trading.storage import connect

    if venue not in VENUES or not 0 <= start < end <= time.time():
        raise ValueError("Invalid received-time replay interval")
    identifier = request_id or digest([venue, start, end, strategy])
    results = Results(root / "results.sqlite3")
    existing = results.run(run_id=identifier)
    if existing:
        if existing["status"] == "starting":
            results.status(identifier, "failed", "Interrupted replay; create a new request")
            return results.run(run_id=identifier)
        return existing
    config = run_config("backtest-" + venue, "backtest", strategy) | {
        "venue": venue,
        "station_id": "KNYC",
        "execution_version": 3,
        "projection_version": 2,
        "start": start,
        "end": end,
        "execution": "Native L2 received-time replay; no assumed historical receipt times",
    }
    config.pop("config_hash", None)
    config["config_hash"] = digest(config)
    results.create(identifier, "backtest-" + venue, "backtest", config)
    session = Session(config)
    try:
        with connect(root / "feed.sqlite3", readonly=True) as con:
            con.execute("BEGIN")
            # Seed only contract definitions known at start. Quotes require actual interval events.
            seed = con.execute(
                "SELECT * FROM feed_events WHERE kind='contracts' AND key=? AND received<? "
                "ORDER BY seq DESC LIMIT 1",
                (venue, start),
            ).fetchall()
            rows = con.execute(
                "SELECT * FROM feed_events WHERE received>=? AND received<=? ORDER BY seq",
                (start, end),
            )

            def apply(row):
                event = dict(row) | {"data": json.loads(row["body"])}
                for native in feed_event(session, event):
                    session.apply(native)

            for row in seed:
                apply(row)
            session.apply(
                {"kind": "start", "ts": max(int(start * 1e9), session.now + 1), "data": {}}
            )
            predictions = 0
            for row in rows:
                predictions += row["kind"] == "prediction" and row["key"] == venue
                apply(row)
        snapshot = session.snapshot()
        snapshot["prediction_events"] = predictions
        if not predictions:
            snapshot["coverage"] = "No station model events; not an executable strategy backtest"
        seq = results.append(identifier, "replay-final", {"kind": "replay", "config": config})
        results.checkpoint(identifier, seq, snapshot, "completed" if predictions else "no-data")
        return results.run(run_id=identifier)
    except Exception as exc:
        results.status(identifier, "failed", type(exc).__name__)
        raise
    finally:
        session.dispose()


def backtest_worker(root, once=False):
    requests = Requests(root / "requests" / "requests.sqlite3")
    with single_writer(root / "us-backtests.lock"):
        while True:
            for venue in VENUES:
                for request in requests.pending("backtest-" + venue, "backtest"):
                    if request["expires"] < time.time():
                        requests.finish(request["request_id"], "rejected", "Request expired")
                        continue
                    try:
                        body = json.loads(request["payload"])
                        run = replay(
                            root,
                            venue,
                            body["start"],
                            body["end"],
                            body.get("strategy", "S1_S2_S3"),
                            request["request_id"],
                        )
                        requests.finish(request["request_id"], run["status"])
                    except (ValueError, KeyError, RuntimeError) as exc:
                        requests.finish(request["request_id"], "failed", str(exc))
            if once:
                return
            time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--venue", choices=VENUES, default="kalshi")
    parser.add_argument("--backtests", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.backtests:
        backtest_worker(args.root, args.once)
    else:
        paper(args.root, args.venue, args.once)

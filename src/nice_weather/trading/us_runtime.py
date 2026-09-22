"""US venue paper worker using native Nautilus facts and existing recovery."""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path

from nice_weather.trading.feed import FeedStore
from nice_weather.trading.recovery import PaperRunner
from nice_weather.trading.storage import Requests, Results, connect, digest, single_writer
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
    if event["kind"] == "settlement" and event["key"] in session.metadata:
        from nice_weather.trading.us_markets import final_value

        contract = session.metadata[event["key"]]
        evidence = event["data"]["source_payload"]
        payout = final_value(contract, evidence, event["received"])
        result = []
        for token, value in ((contract["yes_token_id"], payout),
                             (contract["no_token_id"], 1 - payout)):
            if token in session.outcomes:
                if session.outcomes[token] != float(value):
                    raise ValueError("Final settlement changed; account reconciliation required")
                continue
            result.append({"kind": "settlement", "ts": ts + len(result), "data": {
                "token_id": token, "value": float(value), "evidence_type": "official_final",
                "source_hash": digest(evidence), "source_payload": evidence,
                "received_at": event["received"], "capture_ids": event["data"]["capture_ids"],
            }})
        return result
    return []


def paper(root, venue, once=False):
    account = f"sandbox-{venue}-knyc"
    results = Results(root / "results.sqlite3")
    requests = Requests(root / "requests" / "requests.sqlite3")
    feed = FeedStore(root / "feed.sqlite3")
    # No transaction is held; avoid a checkpoint on every short-lived writer close.
    with (single_writer(root / (account + ".lock")), connect(results.path),
          connect(requests.path, readonly=True) as request_reader):
        run = results.run(account=account)
        if not run:
            config = run_config(account, "sandbox", "S1_S2_S3") | {
                "venue": venue,
                "station_id": "KNYC",
                "execution_version": 3,
                "signal_version": "knyc-executable-v2",
                "strategy_version": "knyc-executable-v2",
                "projection_version": 2,
                "feed_cursor": 0,
                "execution": "Native L2 IOC/GTC; received-time snapshots; no maker rebates",
            }
            config.pop("config_hash", None)
            config["config_hash"] = digest(config)
            results.create(account, account, "sandbox", config)
            run = results.run(account=account)
        runner = PaperRunner(results, run)
        if runner.session.config.get("signal_version") != "knyc-executable-v2":
            runner.session.config.update(signal_version="knyc-executable-v2",
                                         strategy_version="knyc-executable-v2")
            runner.session.config["config_hash"] = digest({
                k: v for k, v in runner.session.config.items() if k != "config_hash"
            })
            # Existing v1 trigger records keep their consumed semantics; account facts stay intact.
            runner.commit("signal-v2-upgrade")
        last_heartbeat = float("-inf")
        try:
            while True:
                cursor = runner.session.config.get("feed_cursor", 0)
                events = feed.since(cursor)
                for event in events:
                    native_events = feed_event(runner.session, event)
                    for native in native_events:
                        runner.session.apply(native)
                    runner.session.config["feed_cursor"] = event["seq"]
                    if native_events:
                        # Own-venue state and fills remain immediately durable. Foreign
                        # events only advance the cursor, saved by the next heartbeat.
                        runner.commit(
                            f"feed-{event['seq']}" if event["kind"] == "prediction" else None
                        )
                if time.monotonic() - last_heartbeat >= 1:
                    now = max(time.time_ns(), runner.session.now + 1)
                    runner.apply("clock", {"kind": "clock", "ts": now, "data": {}})
                    last_heartbeat = time.monotonic()
                for request in requests.pending(account, "sandbox", connection=request_reader):
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
                if once:
                    return
                time.sleep(0.05 if events else 0.2)
        except BaseException as exc:
            results.status(runner.run_id, "paused", type(exc).__name__)
            raise
        finally:
            runner.session.dispose()


def replay(root, venue, start, end, strategy="S1_S2_S3", request_id=None, *,
           cash=100, simulation=None):
    """Replay original receipts, not final labels or reconstructed receipt times."""
    from nice_weather.trading.backtest_view import ReplayView
    from nice_weather.trading.engine import Session
    from nice_weather.trading.storage import connect

    if venue not in VENUES or not 0 <= start < end <= time.time():
        raise ValueError("Invalid received-time replay interval")
    execution = {}
    if simulation is not None:
        from nice_weather.trading.paper_execution import VERSION, settings

        execution = {"simulation": settings(simulation), "execution_model": VERSION,
                     "execution": "Market-price approximation; received-time replay"}
    identifier = request_id or digest([venue, start, end, strategy, "knyc-executable-v2",
                                      cash, execution])
    results = Results(root / "results.sqlite3")
    existing = results.run(run_id=identifier)
    if existing:
        if existing["status"] in {"starting", "running"}:
            results.status(identifier, "failed", "Interrupted replay; create a new request")
            return results.run(run_id=identifier)
        return existing
    config = run_config("backtest-" + venue, "backtest", strategy, cash=cash) | {
        "venue": venue,
        "station_id": "KNYC",
        "execution_version": 3,
        "signal_version": "knyc-executable-v2",
        "strategy_version": "knyc-executable-v2",
        "projection_version": 2,
        "start": start,
        "end": end,
        "execution": "Native L2 received-time replay; no assumed historical receipt times",
    } | execution
    config.pop("config_hash", None)
    config["config_hash"] = digest(config)
    results.create(identifier, "backtest-" + venue, "backtest", config)
    session = Session(config)
    view = ReplayView()
    try:
        with connect(root / "feed.sqlite3", readonly=True) as con:
            # Seed only contract definitions known at start. Quotes require actual interval events.
            seed = con.execute(
                "SELECT * FROM feed_events WHERE kind='contracts' AND key=? AND received<? "
                "ORDER BY seq DESC LIMIT 1",
                (venue, start),
            ).fetchall()
            ceiling = con.execute("SELECT COALESCE(MAX(seq),0) FROM feed_events").fetchone()[0]

        audit = {"requested_start": start, "requested_end": end, "feed_ceiling": ceiling,
                 "scan_complete": False,
                 "cursor": 0, "inputs": {}, "decisions": {}, "decision_changes": 0}
        previous_signals, pending_decisions = {}, []

        def apply(row, *, seed=False):
            event = dict(row) | {"data": json.loads(row["body"])}
            native_events = feed_event(session, event)
            if native_events and not seed:
                group = audit["inputs"].setdefault(row["kind"], {
                    "count": 0, "first_received": row["received"],
                    "last_received": row["received"], "max_gap_seconds": 0,
                })
                group["count"] += 1
                group["max_gap_seconds"] = max(
                    group["max_gap_seconds"], row["received"] - group["last_received"])
                group["first_received"] = min(group["first_received"], row["received"])
                group["last_received"] = max(group["last_received"], row["received"])
                if row["kind"] == "prediction":
                    reasons = group.setdefault("input_reasons", {})
                    reason = event["data"].get("reason") or "not_reported"
                    reasons[reason] = reasons.get(reason, 0) + 1
            for index, native in enumerate(native_events):
                session.apply(native)
                if not seed:
                    view.observe_session(session)
                for strategy_id, signal in session.signals.items():
                    signature = digest(signal)
                    if previous_signals.get(strategy_id) == signature:
                        continue
                    previous_signals[strategy_id] = signature
                    pending_decisions.append({"feed_seq": row["seq"], "native_index": index,
                                              "received_at": row["received"],
                                              "signal": json.loads(json.dumps(signal))})
                    reasons = audit["decisions"].setdefault(strategy_id, Counter())
                    reasons[signal.get("reason") or signal["action"]] += 1
                    audit["decision_changes"] += 1

        for row in seed:
            apply(row, seed=True)
        session.apply(
            {"kind": "start", "ts": max(int(start * 1e9), session.now + 1), "data": {}}
        )
        view.observe(session.snapshot(), force=True)
        predictions, cursor, last_progress = 0, 0, float("-inf")
        # Immutable receipt events and a fixed ceiling allow short read transactions.
        # Release each WAL snapshot before running the potentially slow native replay.
        while cursor < ceiling:
            with connect(root / "feed.sqlite3", readonly=True) as con:
                rows = con.execute(
                    "SELECT * FROM feed_events WHERE seq>? AND seq<=? "
                    "AND received>=? AND received<=? ORDER BY seq LIMIT 256",
                    (cursor, ceiling, start, end),
                ).fetchall()
            if not rows:
                break
            for row in rows:
                predictions += row["kind"] == "prediction" and row["key"] == venue
                apply(row)
            cursor = rows[-1]["seq"]
            view.flush(results, identifier, cursor)
            audit["cursor"] = cursor
            if pending_decisions:
                results.append(identifier, f"replay-decisions-{cursor}", {
                    "kind": "replay-decisions", "decisions": pending_decisions})
                pending_decisions.clear()
            if time.monotonic() - last_progress >= 5:
                # Compact progress only; no full native snapshot or per-event DB write.
                with connect(results.path) as con:
                    con.execute("UPDATE runs SET status='running',snapshot=?,updated=? "
                                "WHERE run_id=?", (json.dumps({"replay_audit": audit}),
                                                   time.time(), identifier))
                last_progress = time.monotonic()
        snapshot = session.snapshot()
        view.observe(snapshot, force=True)
        view.flush(results, identifier, "final")
        snapshot["prediction_events"] = predictions
        audit["scan_complete"] = True
        snapshot["replay_audit"] = audit
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
    with (single_writer(root / "us-backtests.lock"),
          connect(requests.path, readonly=True) as request_reader):
        while True:
            for venue in VENUES:
                for request in requests.pending("backtest-" + venue, "backtest",
                                                connection=request_reader):
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
                            cash=body.get("cash", 100), simulation=body.get("simulation"),
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

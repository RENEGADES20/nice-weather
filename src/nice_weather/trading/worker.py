from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from nice_weather.trading import ENGINE_VERSION, STRATEGIES, validate_strategy
from nice_weather.trading.dataset import contracts, coverage, load_dataset, timestamp
from nice_weather.trading.storage import Requests, Results, connect, digest, single_writer


def refresh_request_quote(runner, request):
    from nice_weather.adapters.polymarket import PolymarketReadOnlyAdapter
    from nice_weather.market_stream import MarketStreamCollector

    payload = json.loads(request["payload"])
    token = payload.get("token")
    if token not in runner.session.metadata:
        return
    with PolymarketReadOnlyAdapter() as adapter:
        books = adapter.fetch_books_batch_payload([token])
    for book in books:
        if str(book.get("asset_id") or book.get("token_id")) != token:
            continue
        received = time.time_ns()
        row = MarketStreamCollector._book_changes(book) | {
            "token_id": token,
            "source": "clob_rest",
            "event_kind": "snapshot",
            "status": "available",
            "received_at": datetime.now(UTC).isoformat(),
            "exchange_event_at": book.get("timestamp"),
            "source_payload": book,
        }
        runner.apply(
            "request-quote-" + request["request_id"],
            {"kind": "quote", "ts": max(received, runner.session.now), "data": row},
        )


def poll_final_results(runner):
    import httpx

    conditions = {p["condition"] for p in runner.session.snapshot()["positions"]}
    for condition in conditions:
        if not re.fullmatch(r"0x[0-9a-fA-F]{64}", condition):
            continue
        url = "https://clob.polymarket.com/markets/" + condition
        try:
            response = httpx.get(url, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            continue  # Retain an unsettled position; no guessed resolution.
        tokens = payload.get("tokens", [])
        if (
            payload.get("closed") is not True
            or payload.get("condition_id") != condition
            or len(tokens) != 2
            or sum(t.get("winner") is True for t in tokens) != 1
        ):
            continue
        for token in tokens:
            token_id = str(token.get("token_id"))
            if token_id not in runner.session.metadata or token_id in runner.session.outcomes:
                continue
            data = {
                "token_id": token_id,
                "value": int(token.get("winner") is True),
                "evidence_type": "official_final",
                "source_hash": digest(payload),
                "source_url": url,
                "source_payload": payload,
            }
            runner.apply(
                "final-" + token_id,
                {
                    "kind": "settlement",
                    "ts": max(time.time_ns(), runner.session.now + 1),
                    "data": data,
                },
            )


def run_config(
    account, mode, strategy_id="noop", parameters=None, tokens=None, cash=100, dataset_id=None
):
    parameters = validate_strategy(strategy_id, parameters or {}, mode)
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        sha = os.environ.get("NICE_WEATHER_CODE_SHA", "unavailable")
    source_root = Path(__file__).resolve().parents[1]
    source_hash = hashlib.sha256()
    for path in sorted(source_root.rglob("*.py")):
        source_hash.update(path.relative_to(source_root).as_posix().encode())
        source_hash.update(path.read_bytes())
    config = {
        "account": account,
        "mode": mode,
        "strategy_id": strategy_id,
        "strategy_version": STRATEGIES[strategy_id]["version"],
        "parameters": parameters,
        "tokens": tokens or [],
        "cash": cash,
        "dataset_id": dataset_id,
        "engine_version": ENGINE_VERSION,
        "projection_version": 2,
        "code_sha": sha,
        "source_sha256": source_hash.hexdigest(),
        "execution": "L1 depth consumption; no maker queue priority; no rebates",
    }
    config["config_hash"] = digest(config)
    return config


class Runner:
    def __init__(self, results: Results, run: dict):
        from nice_weather.trading.engine import Session

        self.results, self.run_id = results, run["run_id"]
        results.status(self.run_id, "recovering")
        self.session = Session(json.loads(run["config"]))
        self.seen = set()
        # ponytail: full session replay is O(n); native checkpoints when session length requires it.
        try:
            for entry in results.inputs(self.run_id):
                event = json.loads(entry["body"])
                snapshot = self.session.apply(event)
                if entry["snapshot_hash"] and digest(snapshot) != entry["snapshot_hash"]:
                    raise RuntimeError(f"Recovery mismatch at input {entry['seq']}; account paused")
                if snapshot is not None and not entry["snapshot_hash"]:
                    results.checkpoint(self.run_id, entry["seq"], snapshot, status="recovering")
                self.seen.add(entry["input_id"])
            results.status(self.run_id, "running")
        except Exception as exc:
            results.status(self.run_id, "paused", str(exc))
            self.session.dispose()
            raise

    def apply(self, input_id, event):
        if input_id in self.seen:
            return self.session.snapshot()
        seq = self.results.append(self.run_id, input_id, event)
        if seq is None:
            raise RuntimeError("Input already journaled but not replayed; restart required")
        try:
            snapshot = self.session.apply(event)
            if snapshot is None:
                snapshot = self.session.snapshot()
            self.results.checkpoint(self.run_id, seq, snapshot)
            self.seen.add(input_id)
            return snapshot
        except Exception as exc:
            self.results.status(self.run_id, "paused", str(exc))
            raise


def backtest(root: Path, request: dict, *, cancelled=lambda: False) -> str:
    requests, results = (
        Requests(root / "requests" / "requests.sqlite3"),
        Results(root / "results.sqlite3"),
    )
    run_id = request["request_id"]
    payload = json.loads(request["payload"])
    # A dataset is selected by hash inside the configured directory, never an arbitrary UI path.
    dataset_id = payload["dataset_id"]
    if len(dataset_id) != 64 or any(c not in "0123456789abcdef" for c in dataset_id):
        raise ValueError("Invalid dataset ID")
    dataset = load_dataset(root / "datasets" / (dataset_id + ".json"))
    parameters = validate_strategy(
        payload["strategy_id"], payload.get("parameters", {}), "backtest"
    )
    check = coverage(dataset, payload["tokens"], parameters.get("require_both", False))
    if check["rejection_reasons"]:
        raise ValueError("; ".join(check["rejection_reasons"]))
    config = run_config(
        request["account"],
        "backtest",
        payload["strategy_id"],
        parameters,
        payload["tokens"],
        payload.get("cash", 100),
        dataset_id,
    )
    if results.run(run_id):
        raise ValueError("Run ID already exists; use a new request to rerun")
    results.create(run_id, request["account"], "backtest", config)
    runner = Runner(results, results.run(run_id))
    try:
        selected = set(payload["tokens"])
        required = set(selected)
        for event in dataset["events"]:
            if event["kind"] == "contract":
                row = event["data"]
                pair = {row["yes_token_id"], row["no_token_id"]}
                if pair & selected and parameters.get("require_both"):
                    required.update(pair)
        for i, event in enumerate(dataset["events"]):
            if i % 64 == 0 and cancelled():
                results.status(run_id, "cancelled", "Partial result; excluded from completed runs")
                requests.finish(run_id, "cancelled")
                return run_id
            row = event["data"]
            if event["kind"] == "contract":
                if not {row["yes_token_id"], row["no_token_id"]} & required:
                    continue
            elif row.get("token_id") not in required:
                continue
            if not runner.seen:
                runner.apply("strategy-start", {"kind": "start", "ts": event["ts"], "data": {}})
            runner.apply(f"dataset-{i}", event)
        if cancelled():
            results.status(run_id, "cancelled", "Partial result; excluded from completed runs")
            requests.finish(run_id, "cancelled")
            return run_id
        runner.apply("dataset-end", {"kind": "clock", "ts": timestamp(dataset["end"]), "data": {}})
        results.status(run_id, "completed")
        requests.finish(run_id, "completed")
    except Exception as exc:
        results.status(run_id, "failed", str(exc))
        requests.finish(run_id, "failed", str(exc))
        raise
    finally:
        runner.session.dispose()
    return run_id


def backtest_worker(root: Path, *, once=False):
    requests = Requests(root / "requests" / "requests.sqlite3")
    results = Results(root / "results.sqlite3")
    with single_writer(root / "backtest.lock"):
        with connect(requests.path) as con:
            interrupted = list(
                con.execute(
                    "SELECT request_id FROM requests WHERE mode='backtest' AND status='running'"
                )
            )
        for row in interrupted:
            results.status(row[0], "interrupted", "Worker interrupted; explicitly rerun")
            requests.finish(row[0], "interrupted", "Worker interrupted; explicitly rerun")
        while True:
            with connect(requests.path, readonly=True) as con:
                queued = [
                    dict(r)
                    for r in con.execute(
                        "SELECT * FROM requests WHERE mode='backtest' AND status='queued' "
                        "AND kind='backtest' ORDER BY created,request_id"
                    )
                ]
            for request in queued:

                def cancelled(request=request):
                    with connect(requests.path, readonly=True) as con:
                        matches = [
                            dict(r)
                            for r in con.execute(
                                "SELECT * FROM requests WHERE kind='cancel_run' "
                                "AND status='queued' "
                                "AND mode='backtest' AND account=?",
                                (request["account"],),
                            )
                        ]
                    for match in matches:
                        if json.loads(match["payload"]).get("run_id") == request["request_id"]:
                            requests.finish(match["request_id"], "completed")
                            return True
                    return False

                if request["expires"] < time.time():
                    requests.finish(request["request_id"], "expired")
                    continue
                requests.finish(request["request_id"], "running")
                try:
                    backtest(root, request, cancelled=cancelled)
                except Exception as exc:
                    requests.finish(request["request_id"], "failed", str(exc))
            if once:
                return
            time.sleep(1)


def sandbox_worker(
    root: Path,
    source: Path,
    account="sandbox-001",
    *,
    once=False,
    strategy_id="noop",
    parameters=None,
    tokens=None,
):
    requests, results = (
        Requests(root / "requests" / "requests.sqlite3"),
        Results(root / "results.sqlite3"),
    )
    if not re.fullmatch(r"sandbox-[A-Za-z0-9_-]{1,64}", account):
        raise ValueError("Sandbox account must have sandbox- prefix")
    with single_writer(root / (account + ".lock")):
        run = results.run(account=account)
        if run is None:
            run_id = str(uuid.uuid4())
            config = run_config(account, "sandbox", strategy_id, parameters, tokens)
            config["started_ns"] = time.time_ns()
            results.create(run_id, account, "sandbox", config)
            run = results.run(run_id)
        runner = Runner(results, run)
        if runner.session.projection_version < 2:
            runner.apply(
                "projection-upgrade-2",
                {
                    "kind": "projection_upgrade",
                    "ts": max(time.time_ns(), runner.session.now + 1),
                    "data": {"version": 2, "reason": "Include native closed NETTING lifecycles"},
                },
            )
        start = json.loads(run["config"])["started_ns"]
        cursor = 0
        contract_cursor = "1970-01-01T00:00:00+00:00"
        next_resolution_check = 0.0
        next_position_refresh = 0.0
        for entry in results.inputs(run["run_id"]):
            event = json.loads(entry["body"])
            cursor = max(cursor, event.get("cursor", 0), event.get("data", {}).get("source_seq", 0))
        try:
            while True:
                now = datetime.now(UTC).isoformat()
                if source.exists():
                    with connect(source, readonly=True) as con:
                        definitions = contracts(
                            con,
                            now,
                            since=contract_cursor,
                            latest_only=contract_cursor == "1970-01-01T00:00:00+00:00",
                        )
                        contract_cursor = now
                        ticks = [
                            dict(r)
                            for r in con.execute(
                                "SELECT rowid AS source_seq,* FROM market_top_ticks "
                                "WHERE rowid>? AND julianday(received_at)>=julianday(?) "
                                "ORDER BY rowid LIMIT 64",
                                (
                                    cursor,
                                    datetime.fromtimestamp(
                                        max(start, time.time_ns() - 30_000_000_000) / 1e9, UTC
                                    ).isoformat(),
                                ),
                            )
                        ]
                        if not ticks:
                            cursor = con.execute(
                                "SELECT COALESCE(MAX(rowid),0) FROM market_top_ticks"
                            ).fetchone()[0]
                    for definition in definitions:
                        input_id = "contract-" + digest(definition)
                        if input_id not in runner.seen:
                            runner.apply(
                                input_id,
                                {
                                    "kind": "contract",
                                    "ts": max(time.time_ns(), runner.session.now + 1),
                                    "data": definition,
                                },
                            )
                    for tick in ticks:
                        cursor = tick["source_seq"]
                        latest = runner.session.quotes.get(tick["token_id"])
                        if (
                            timestamp(tick["received_at"]) < start
                            or tick["source"] != "clob_ws"
                            or tick.get("event_kind") not in {"quote", "snapshot", "disconnect"}
                            or tick["token_id"] not in runner.session.metadata
                            or timestamp(tick["received_at"]) < time.time_ns() - 30_000_000_000
                            or (latest and timestamp(tick["received_at"]) < latest["ts"])
                        ):
                            continue
                        runner.apply(
                            "tick-" + tick["tick_id"],
                            {
                                "kind": "quote",
                                "ts": max(timestamp(tick["received_at"]), runner.session.now),
                                "data": tick,
                            },
                        )
                if time.monotonic() >= next_position_refresh:
                    for position in runner.session.snapshot()["positions"]:
                        try:
                            refresh_request_quote(
                                runner,
                                {
                                    "request_id": f"position-{position['token']}-{time.time_ns()}",
                                    "payload": json.dumps({"token": position["token"]}),
                                },
                            )
                        except Exception:
                            pass  # Missing valuation stays explicitly unavailable.
                    next_position_refresh = time.monotonic() + 15
                for request in requests.pending(account, "sandbox"):
                    input_id = "request-" + request["request_id"]
                    if input_id not in runner.seen and request["expires"] < time.time():
                        requests.finish(request["request_id"], "expired")
                        continue
                    if input_id not in runner.seen and request["kind"] in {
                        "order",
                        "close",
                        "replace",
                    }:
                        try:
                            refresh_request_quote(runner, request)
                        except Exception as exc:
                            requests.finish(
                                request["request_id"],
                                "rejected",
                                f"Executable book refresh failed: {type(exc).__name__}",
                            )
                            continue
                        if request["expires"] < time.time():
                            requests.finish(request["request_id"], "expired")
                            continue
                    snapshot = (
                        runner.apply(
                            input_id,
                            {
                                "kind": request["kind"],
                                "data": json.loads(request["payload"]),
                                "request_id": request["request_id"],
                                "ts": max(time.time_ns(), runner.session.now + 1),
                            },
                        )
                        if input_id not in runner.seen
                        else runner.session.snapshot()
                    )
                    rejection = next(
                        (
                            r
                            for r in snapshot["rejections"]
                            if r["request_id"] == request["request_id"]
                        ),
                        None,
                    )
                    requests.finish(
                        request["request_id"],
                        "rejected" if rejection else "completed",
                        rejection["reason"] if rejection else None,
                    )
                clock_ns = max(time.time_ns(), runner.session.now + 1)
                runner.apply(
                    f"clock-{clock_ns}",
                    {"kind": "clock", "ts": clock_ns, "cursor": cursor, "data": {}},
                )
                if time.monotonic() >= next_resolution_check:
                    poll_final_results(runner)
                    next_resolution_check = time.monotonic() + 60
                if once:
                    return
                time.sleep(1)
        finally:
            runner.session.dispose()

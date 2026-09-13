from __future__ import annotations

import json
import uuid
from pathlib import Path

from nice_weather.trading.storage import Requests, connect


def register(subparsers):
    for name in ("trading", "backtest"):
        parser = subparsers.add_parser(name, help="Isolated Nautilus workbench")
        parser.add_argument("action", choices=("run", "status", "worker", "export", "submit"))
        parser.add_argument("--root", type=Path, default=Path("var/trading"))
        parser.add_argument("--db", type=Path)
        parser.add_argument("--request", type=Path)
        parser.add_argument(
            "--account", default="sandbox-001" if name == "trading" else "backtest-001"
        )
        parser.add_argument("--mode", choices=("sandbox", "live"), default="sandbox")
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--start")
        parser.add_argument("--end")
        parser.add_argument("--strategy", default="noop")
        parser.add_argument("--tokens", nargs="*", default=[])


def execute(args):
    if args.action == "status":
        if args.mode == "live":
            from nice_weather.trading.live import status

            print(json.dumps(status(), indent=2))
        elif (args.root / "results.sqlite3").exists():
            with connect(args.root / "results.sqlite3", readonly=True) as con:
                print(json.dumps([dict(r) for r in con.execute("SELECT * FROM runs")], indent=2))
        else:
            print('{"status":"not_started"}')
        return 0
    if args.mode == "live":
        raise ValueError("LIVE is disabled for this release; credentials are not loaded")
    if args.action == "export":
        from nice_weather.trading.dataset import export_dataset

        if not args.db or not args.start or not args.end:
            raise ValueError("export requires --db --start --end (timezone-qualified timestamps)")
        print(
            export_dataset(
                args.db, args.root / "datasets", args.start, args.end, args.root / "results.sqlite3"
            )
        )
    elif args.command == "trading" and args.action in {"run", "worker"}:
        from nice_weather.trading.worker import sandbox_worker

        if not args.db:
            raise ValueError("Trading worker requires read-only --db source")
        sandbox_worker(
            args.root,
            args.db,
            args.account,
            once=args.once,
            strategy_id=args.strategy,
            tokens=args.tokens,
        )
    elif args.command == "backtest" and args.action == "worker":
        from nice_weather.trading.worker import backtest_worker

        backtest_worker(args.root, once=args.once)
    else:
        if not args.request:
            raise ValueError("--request JSON file required")
        body = json.loads(args.request.read_text(encoding="utf-8"))
        mode = "backtest" if args.command == "backtest" else "sandbox"
        request_id = body.get("request_id", str(uuid.uuid4()))
        Requests(args.root / "requests" / "requests.sqlite3").submit(
            request_id,
            args.account,
            mode,
            "backtest" if mode == "backtest" else body["kind"],
            body.get("payload", body),
            ttl=86400 if mode == "backtest" else 60,
        )
        if args.action == "run":
            from nice_weather.trading.worker import backtest_worker

            backtest_worker(args.root, once=True)
        print(request_id)
    return 0

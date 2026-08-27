from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import date, datetime
from pathlib import Path

from nice_weather.adapters.polymarket import PolymarketReadOnlyAdapter
from nice_weather.adapters.weather import WeatherReadOnlyAdapter
from nice_weather.collector import WeatherCollector
from nice_weather.config import load_city_config
from nice_weather.domain import RunMode, utc_now
from nice_weather.queries import DashboardQuery
from nice_weather.r2_archive import R2Archive
from nice_weather.runner import run_fixture_once, run_live_once
from nice_weather.store import WeatherStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nice-weather")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("db-init", help="Initialize the SQLite database")
    init_parser.add_argument("--db", type=Path, required=True)

    summary_parser = subparsers.add_parser("db-summary", help="Show database table counts")
    summary_parser.add_argument("--db", type=Path, required=True)

    config_parser = subparsers.add_parser("config-check", help="Validate NYC/KLGA config")
    config_parser.add_argument("--config", type=Path)

    run_once = subparsers.add_parser("run-once", help="Run one decision cycle")
    run_once.add_argument("--mode", choices=("fixture", "shadow", "paper"), required=True)
    run_once.add_argument("--fixture", type=Path)
    run_once.add_argument("--db", type=Path, required=True)
    run_once.add_argument("--config", type=Path)
    run_once.add_argument("--city", choices=("NYC",), default="NYC")

    run_loop = subparsers.add_parser("run-loop", help="Run bounded-frequency live cycles")
    run_loop.add_argument("--mode", choices=("shadow", "paper"), required=True)
    run_loop.add_argument("--db", type=Path, required=True)
    run_loop.add_argument("--config", type=Path)
    run_loop.add_argument("--city", choices=("NYC",), default="NYC")
    run_loop.add_argument("--interval-seconds", type=int, default=60)
    run_loop.add_argument("--max-cycles", type=int)

    smoke = subparsers.add_parser("smoke", help="Run one read-only source smoke test")
    smoke.add_argument(
        "--target", choices=("polymarket", "observations", "forecast", "dashboard"), required=True
    )
    smoke.add_argument("--city", choices=("NYC",), default="NYC")
    smoke.add_argument("--config", type=Path)
    smoke.add_argument("--db", type=Path)

    collect = subparsers.add_parser("collect-weather", help="Collect KLGA weather source versions")
    collect.add_argument("--db", type=Path, required=True)
    collect.add_argument("--config", type=Path)
    collect.add_argument("--once", action="store_true")
    collect.add_argument("--skip-settlement", action="store_true")

    r2_check = subparsers.add_parser("r2-check", help="Verify append-only R2 write and read")
    r2_check.add_argument("--db", type=Path, required=True)
    r2_check.add_argument("--config", type=Path)

    r2_sync = subparsers.add_parser("r2-sync", help="Upload pending captures and daily Parquet")
    r2_sync.add_argument("--db", type=Path, required=True)
    r2_sync.add_argument("--config", type=Path)
    r2_sync.add_argument("--local-date", type=date.fromisoformat)
    r2_sync.add_argument("--no-daily", action="store_true")

    status = subparsers.add_parser("collector-status", help="Show source and storage health")
    status.add_argument("--db", type=Path, required=True)
    status.add_argument("--config", type=Path)
    return parser


def _decision_json(decision: object) -> str:
    return json.dumps(
        {
            "decision_id": decision.decision_id,
            "decision_time": decision.decision_time.isoformat(),
            "mode": decision.mode.value,
            "status": decision.status,
            "overall_action": decision.overall_action,
            "health": decision.health_level.value,
            "reason_codes": [code.value for code in decision.reason_codes],
            "approved_bins": [item.label for item in decision.outcomes if item.risk_approved],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _run_smoke(args: argparse.Namespace) -> dict[str, object]:
    config = load_city_config(args.config)
    now = utc_now()
    if args.target == "polymarket":
        with PolymarketReadOnlyAdapter() as adapter:
            snapshot = adapter.discover(config, now)
        events = snapshot.payload.get("events", [])
        return {
            "target": args.target,
            "ok": len(events) == 1,
            "event_ids": [str(event.get("id")) for event in events],
            "received_at": snapshot.received_at.isoformat(),
        }
    if args.target == "observations":
        start = datetime.combine(
            now.astimezone(config.zone).date(), datetime.min.time(), config.zone
        )
        with WeatherReadOnlyAdapter() as adapter:
            snapshot = adapter.fetch_observations(config.station_id, start, now)
        return {
            "target": args.target,
            "ok": bool(snapshot.payload.get("observations")),
            "count": len(snapshot.payload.get("observations", [])),
            "received_at": snapshot.received_at.isoformat(),
        }
    if args.target == "forecast":
        with WeatherReadOnlyAdapter() as adapter:
            snapshots = adapter.fetch_forecast(config, now.astimezone(config.zone).date(), now)
        hourly = next(snapshot for snapshot in snapshots if snapshot.kind == "hourly_forecast")
        return {
            "target": args.target,
            "ok": bool(hourly.payload.get("properties", {}).get("periods")),
            "count": len(hourly.payload.get("properties", {}).get("periods", [])),
            "received_at": hourly.received_at.isoformat(),
        }
    if args.db is None:
        raise SystemExit("--db is required for dashboard smoke")
    summary = DashboardQuery(args.db).get_latest_decision_summary()
    return {
        "target": args.target,
        "ok": summary is not None,
        "decision_id": summary["decision_id"] if summary else None,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "db-init":
        with WeatherStore(args.db) as store:
            store.init_schema()
            print(json.dumps({"database": str(store.path), "tables": store.table_counts()}))
        return 0
    if args.command == "db-summary":
        with WeatherStore(args.db, read_only=True) as store:
            print(json.dumps({"database": str(store.path), "tables": store.table_counts()}))
        return 0
    if args.command == "config-check":
        config = load_city_config(args.config)
        print(
            json.dumps(
                {
                    "city": config.city_code,
                    "station": config.station_id,
                    "timezone": config.timezone,
                    "valid": True,
                }
            )
        )
        return 0
    if args.command == "run-once":
        try:
            if args.mode == "fixture":
                if args.fixture is None:
                    raise SystemExit("--fixture is required in fixture mode")
                decision = run_fixture_once(args.fixture, args.db, args.config)
            else:
                decision = run_live_once(RunMode(args.mode.upper()), args.db, args.config)
        except Exception as exc:
            print(
                json.dumps(
                    {"status": "error", "error_type": type(exc).__name__, "message": str(exc)}
                )
            )
            return 2
        print(_decision_json(decision))
        return 0
    if args.command == "run-loop":
        if args.interval_seconds < 5:
            raise SystemExit("--interval-seconds must be at least 5")
        mode = RunMode(args.mode.upper())
        cycle = 0
        try:
            while args.max_cycles is None or cycle < args.max_cycles:
                cycle += 1
                try:
                    decision = run_live_once(mode, args.db, args.config)
                    print(_decision_json(decision), flush=True)
                except Exception as exc:
                    print(
                        json.dumps(
                            {
                                "status": "cycle_error",
                                "cycle": cycle,
                                "error_type": type(exc).__name__,
                                "message": str(exc),
                            }
                        ),
                        flush=True,
                    )
                if args.max_cycles is None or cycle < args.max_cycles:
                    time.sleep(args.interval_seconds)
        except KeyboardInterrupt:
            print(json.dumps({"status": "stopped", "cycles": cycle}))
        return 0
    if args.command == "smoke":
        result = _run_smoke(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["ok"] else 1
    if args.command == "collect-weather":
        config = load_city_config(args.config)
        collector = WeatherCollector(config, str(args.db))
        if args.once:
            results = collector.collect_once(include_settlement=not args.skip_settlement)
            print(json.dumps({"results": results}, ensure_ascii=False, sort_keys=True))
            return 0 if all(result["ok"] for result in results) else 1
        if args.skip_settlement:
            raise SystemExit("--skip-settlement is supported only with --once")
        try:
            collector.run_forever()
        except KeyboardInterrupt:
            print(json.dumps({"status": "stopped"}))
        return 0
    if args.command == "r2-check":
        config = load_city_config(args.config)
        with WeatherStore(args.db) as store:
            store.init_schema()
        result = R2Archive(args.db, config).check()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "r2-sync":
        config = load_city_config(args.config)
        with WeatherStore(args.db) as store:
            store.init_schema()
        result = R2Archive(args.db, config).sync(
            local_date=args.local_date,
            daily_if_due=not args.no_daily,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "collector-status":
        config = load_city_config(args.config)
        with WeatherStore(args.db) as store:
            store.init_schema()
            result = store.collector_status()
        disk = shutil.disk_usage(args.db.resolve().parent)
        result["disk"] = {
            "total_bytes": disk.total,
            "used_bytes": disk.used,
            "free_bytes": disk.free,
        }
        r2 = result["r2"]
        first = r2.get("first_upload")
        if first:
            elapsed_days = max(
                1.0,
                (datetime.now().astimezone() - datetime.fromisoformat(first)).total_seconds()
                / 86400,
            )
            per_day = float(r2["bytes"]) / elapsed_days
            r2["estimated_bytes_per_day"] = round(per_day)
            r2["projected_30_day_bytes"] = round(per_day * 30)
            r2["projected_365_day_bytes"] = round(per_day * 365)
        r2["warning_threshold_bytes"] = config.collector.storage_warning_bytes
        r2["warning"] = int(r2["bytes"]) >= config.collector.storage_warning_bytes
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

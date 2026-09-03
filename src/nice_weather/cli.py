from __future__ import annotations

import argparse
import csv
import io
import json
import os
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import date, datetime
from importlib.metadata import version
from pathlib import Path

from nice_weather.adapters.polymarket import PolymarketReadOnlyAdapter
from nice_weather.adapters.weather import WeatherReadOnlyAdapter
from nice_weather.collector import WeatherCollector
from nice_weather.config import load_city_config
from nice_weather.domain import RunMode, stable_id, utc_now
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

    database = subparsers.add_parser("db", help="Migrate or verify a SQLite database")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    migrate = database_commands.add_parser("migrate")
    migrate.add_argument("--db", type=Path, required=True)
    verify = database_commands.add_parser("verify")
    verify.add_argument("--db", type=Path, required=True)
    clone = database_commands.add_parser("clone-migrate")
    clone.add_argument("--source", type=Path, required=True)
    clone.add_argument("--db", type=Path, required=True)

    repair = subparsers.add_parser(
        "repair-metar-time", help="Append corrected METAR observation timestamps"
    )
    repair.add_argument("--db", type=Path, required=True)
    repair.add_argument("--config", type=Path)

    settlement_repair = subparsers.add_parser(
        "repair-settlement-dates",
        help="Rebuild settlement row object dates and derived labels",
    )
    settlement_repair.add_argument("--db", type=Path, required=True)
    settlement_repair.add_argument("--config", type=Path)
    repair_mode = settlement_repair.add_mutually_exclusive_group(required=True)
    repair_mode.add_argument("--dry-run", action="store_true")
    repair_mode.add_argument("--apply", action="store_true")

    version_parser = subparsers.add_parser("version", help="Show deployed build metadata")
    version_parser.add_argument("--json", action="store_true")
    version_parser.add_argument("--db", type=Path)
    version_parser.add_argument("--config", type=Path)

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

    market_stream = subparsers.add_parser(
        "collect-market-stream", help="Collect CLOB top-of-book changes"
    )
    market_stream.add_argument("--db", type=Path, required=True)
    market_stream.add_argument("--config", type=Path)
    market_stream.add_argument("--discover-once", action="store_true")

    research = subparsers.add_parser("research", help="Run read-only research reports")
    research_commands = research.add_subparsers(dest="research_command", required=True)
    repricing = research_commands.add_parser(
        "tmax-repricing", help="Measure market repricing around Tmax knowledge events"
    )
    repricing.add_argument("--db", type=Path, required=True)
    repricing.add_argument("--config", type=Path)
    repricing.add_argument("--from", dest="start_date", type=date.fromisoformat, required=True)
    repricing.add_argument("--to", dest="end_date", type=date.fromisoformat, required=True)
    repricing.add_argument("--quantity", type=float, default=10.0)
    repricing.add_argument("--thresholds", default="0.80,0.90,0.95,0.99")
    repricing.add_argument("--format", choices=("json", "csv"), default="json")

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

    observer = subparsers.add_parser(
        "observe-deployment", help="Monitor a deployment and roll back persistent failures"
    )
    observer.add_argument("--db", type=Path, required=True)
    observer.add_argument("--config", type=Path)
    observer.add_argument("--previous-sha", required=True)
    observer.add_argument("--backup", type=Path, required=True)
    observer.add_argument("--hours", type=float, default=24.0)
    observer.add_argument("--interval-seconds", type=int, default=300)
    observer.add_argument("--check-only", action="store_true")
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


def _git_sha() -> str:
    configured = os.environ.get("NICE_WEATHER_GIT_SHA")
    if configured:
        return configured
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _repair_metar_times(database: Path, config_path: Path | None) -> dict[str, int]:
    from nice_weather.collector import parse_metar_observed_at

    config = load_city_config(config_path)
    inserted = skipped = 0
    with WeatherStore(database) as store:
        store.init_schema()
        rows = store.connection.execute(
            """
            SELECT * FROM weather_observations
            WHERE source='aviationweather' AND parser_version!='metar-ddhhmmz-v2'
            ORDER BY received_at
            """
        ).fetchall()
        with store.transaction() as connection:
            for row in rows:
                reference_text = (
                    row["provider_received_at"] or row["report_time"] or row["received_at"]
                )
                try:
                    corrected = parse_metar_observed_at(
                        str(row["raw_text"]), datetime.fromisoformat(reference_text)
                    )
                except ValueError:
                    skipped += 1
                    continue
                observation_id = stable_id(
                    "observation", "aviationweather", corrected, row["raw_text"], "repair-v2"
                )
                changed = connection.execute(
                    """
                    INSERT OR IGNORE INTO weather_observations(
                      observation_id,capture_id,legacy_snapshot_id,station_id,
                      observed_at,received_at,
                      temperature_f,raw_text,source,temperature_c,raw_unit,
                      quality_control_json,source_version,revision,local_date,
                      provider_received_at,report_time,revision_type,parser_version,
                      weather_metadata_json,object_timezone,object_local_date
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        observation_id,
                        row["capture_id"],
                        row["legacy_snapshot_id"],
                        row["station_id"],
                        corrected.isoformat(),
                        row["received_at"],
                        row["temperature_f"],
                        row["raw_text"],
                        row["source"],
                        row["temperature_c"],
                        row["raw_unit"],
                        row["quality_control_json"],
                        row["source_version"],
                        1,
                        corrected.astimezone(config.zone).date().isoformat(),
                        row["provider_received_at"],
                        row["report_time"],
                        "timestamp_repair",
                        "metar-ddhhmmz-v2",
                        row["weather_metadata_json"],
                        config.object_timezone,
                        corrected.astimezone(config.zone).date().isoformat(),
                    ),
                ).rowcount
                inserted += int(changed)
    return {"inserted": inserted, "skipped": skipped}


def _clone_migrate(source: Path, target: Path) -> dict[str, object]:
    source = source.resolve()
    target = target.resolve()
    temporary = target.with_suffix(target.suffix + ".migrating")
    if target.exists() or temporary.exists():
        raise RuntimeError("Target and temporary migration paths must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source.as_posix()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True, timeout=30.0)) as source_connection:
        with closing(sqlite3.connect(temporary, timeout=30.0)) as target_connection:
            source_connection.backup(target_connection)
    with WeatherStore(temporary) as store:
        store.init_schema()
        result = store.verify_schema()
        result["tables"] = store.table_counts()
    if not result["ok"]:
        raise RuntimeError(f"Migrated database verification failed; retained at {temporary}")
    temporary.replace(target)
    result["source"] = str(source)
    result["database"] = str(target)
    return result


def _repricing_csv(report: dict[str, object]) -> str:
    events = list(report["events"])
    thresholds = [str(item) for item in report["thresholds"]]
    fields = [
        "event_id",
        "local_day",
        "type",
        "bin_id",
        "label",
        "source",
        "temperature_f",
        "contract_temperature_f",
        "object_time",
        "system_received_at",
        "source_latency_seconds",
        "first_market_move_at",
        "tradable_lead_seconds",
        "pre_mid",
        "pre_best_bid",
        "pre_best_ask",
        "target_quantity",
        "target_ask_vwap",
        "executable_ask_depth",
        "slippage",
        "estimated_fee",
        "paper_pnl",
        "sunset",
        "minutes_to_sunset",
        "solar_elevation",
        "remaining_forecast_tmax_f",
        "weather_covariates",
        "final_official_temperature_f",
        "final_bin_id",
        "higher_bin_later",
        *[f"threshold_{item}" for item in thresholds],
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for event in events:
        row = {key: event.get(key) for key in fields}
        threshold_times = event.get("threshold_times", {})
        for threshold in thresholds:
            row[f"threshold_{threshold}"] = threshold_times.get(threshold)
        row["weather_covariates"] = json.dumps(
            row.get("weather_covariates", {}), sort_keys=True
        )
        writer.writerow(row)
    return output.getvalue()


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
    if args.command == "collect-market-stream":
        from nice_weather.market_stream import MarketStreamCollector

        config = load_city_config(args.config)
        collector = MarketStreamCollector(config, str(args.db))
        if args.discover_once:
            metadata, _ = collector.discover()
            print(
                json.dumps(
                    {"status": "complete", "tokens": len(metadata)},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0
        try:
            collector.run_forever()
        except KeyboardInterrupt:
            print(json.dumps({"status": "stopped"}))
        return 0
    if args.command == "db":
        if args.db_command == "clone-migrate":
            result = _clone_migrate(args.source, args.db)
        elif args.db_command == "migrate":
            with WeatherStore(args.db) as store:
                store.init_schema()
                result = store.verify_schema()
        else:
            with WeatherStore(args.db, read_only=True) as store:
                result = store.verify_schema()
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["ok"] else 1
    if args.command == "repair-metar-time":
        print(json.dumps(_repair_metar_times(args.db, args.config), sort_keys=True))
        return 0
    if args.command == "repair-settlement-dates":
        from nice_weather.research import repair_settlement_dates

        config = load_city_config(args.config)
        result = repair_settlement_dates(args.db, config, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "research":
        from nice_weather.research import tmax_repricing_report

        thresholds = tuple(float(item) for item in args.thresholds.split(","))
        if any(item <= 0 or item >= 1 for item in thresholds):
            raise SystemExit("--thresholds must contain probabilities between 0 and 1")
        if args.start_date > args.end_date:
            raise SystemExit("--from must be on or before --to")
        if args.quantity <= 0:
            raise SystemExit("--quantity must be positive")
        report = tmax_repricing_report(
            args.db,
            load_city_config(args.config),
            start_date=args.start_date,
            end_date=args.end_date,
            quantity=args.quantity,
            thresholds=thresholds,
        )
        print(
            _repricing_csv(report)
            if args.format == "csv"
            else json.dumps(report, ensure_ascii=False, sort_keys=True)
        )
        return 0
    if args.command == "version":
        config = load_city_config(args.config)
        result: dict[str, object] = {
            "package_version": version("nice-weather"),
            "git_sha": _git_sha(),
            "model_version": config.model.version,
            "station_id": config.station_id,
        }
        if args.db:
            with WeatherStore(args.db, read_only=True) as store:
                result["schema_version"] = store.connection.execute(
                    "SELECT version FROM schema_meta"
                ).fetchone()[0]
        print(
            json.dumps(result, sort_keys=True)
            if args.json
            else " ".join(map(str, result.values()))
        )
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
    if args.command == "observe-deployment":
        from nice_weather.deployment import observe_deployment

        result = observe_deployment(
            args.db,
            load_city_config(args.config),
            previous_sha=args.previous_sha,
            backup=args.backup,
            hours=args.hours,
            interval_seconds=args.interval_seconds,
            check_only=args.check_only,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["ok"] else 1
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nice_weather.config import load_city_config
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
    return parser


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
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())


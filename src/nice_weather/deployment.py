from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nice_weather.config import CityConfig
from nice_weather.store import WeatherStore

SERVICES = (
    "nice-weather-collector.service",
    "nice-weather-market-stream.service",
    "nice-weather-r2-sync.timer",
    "nice-weather-dashboard.service",
    "nice-weather-runner.service",
)
STOP_UNITS = (
    "nice-weather-r2-sync.timer",
    "nice-weather-runner.service",
    "nice-weather-market-stream.service",
    "nice-weather-collector.service",
    "nice-weather-r2-sync.service",
    "nice-weather-dashboard.service",
)


def _parse(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return result if result.tzinfo is not None else result.replace(tzinfo=UTC)


def deployment_health(
    database: str | Path, config: CityConfig, *, now: datetime | None = None
) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    failures = []
    with WeatherStore(database, read_only=True) as store:
        schema = store.verify_schema()
        if not schema["ok"]:
            failures.append("database_verification")
        bad_settlement_dates = 0
        for row in store.connection.execute(
            "SELECT observed_at,object_local_date FROM settlement_rows"
        ):
            expected = _parse(str(row["observed_at"])).astimezone(config.zone).date().isoformat()
            bad_settlement_dates += int(row["object_local_date"] != expected)
        if bad_settlement_dates:
            failures.append("settlement_object_date")
        bad_market_dates = 0
        for row in store.connection.execute(
            "SELECT exchange_event_at,object_local_date FROM market_top_ticks"
        ):
            expected = (
                _parse(str(row["exchange_event_at"])).astimezone(config.zone).date().isoformat()
            )
            bad_market_dates += int(row["object_local_date"] != expected)
        if bad_market_dates:
            failures.append("market_object_date")
        since = (now - timedelta(hours=1)).isoformat()
        locks = store.connection.execute(
            """
            SELECT COUNT(*) FROM system_events WHERE occurred_at>=?
              AND lower(message) LIKE '%database%locked%'
            """,
            (since,),
        ).fetchone()[0]
        if locks:
            failures.append("sqlite_lock")
        r2_failed = store.connection.execute(
            "SELECT COUNT(*) FROM r2_exports WHERE status='failed'"
        ).fetchone()[0]
        pending_deadline = (
            now - timedelta(seconds=2 * config.collector.r2_sync_interval_seconds)
        ).isoformat()
        stale_pending = store.connection.execute(
            "SELECT COUNT(*) FROM r2_exports WHERE status='pending' AND created_at<?",
            (pending_deadline,),
        ).fetchone()[0]
        if r2_failed:
            failures.append("r2_failed")
        if stale_pending:
            failures.append("r2_pending_stale")
        local_now = now.astimezone(config.zone)
        active_window = (
            config.collector.metar_active_start_hour
            <= local_now.hour
            < config.collector.metar_active_end_hour
        )
        active_contract = store.connection.execute(
            """
            SELECT 1 FROM contract_versions WHERE local_day=? AND event_active=1
              AND event_closed=0 LIMIT 1
            """,
            (local_now.date().isoformat(),),
        ).fetchone()
        latest_market = store.connection.execute(
            "SELECT MAX(received_at) FROM market_top_ticks WHERE source='clob_ws'"
        ).fetchone()[0]
        market_stale = bool(
            active_window
            and active_contract
            and (
                latest_market is None
                or now - _parse(str(latest_market)) > timedelta(minutes=10)
            )
        )
        if market_stale:
            failures.append("market_stream_stale")
    inactive_services = []
    if os.name != "nt":
        for service in SERVICES:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", service], check=False
            )
            if result.returncode:
                inactive_services.append(service)
        if inactive_services:
            failures.append("service_inactive")
    return {
        "ok": not failures,
        "checked_at": now.isoformat(),
        "failures": failures,
        "schema": schema,
        "bad_settlement_dates": bad_settlement_dates,
        "bad_market_dates": bad_market_dates,
        "sqlite_locks_last_hour": locks,
        "r2_failed": r2_failed,
        "r2_stale_pending": stale_pending,
        "market_stream_stale": market_stale,
        "inactive_services": inactive_services,
    }


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def rollback_deployment(
    database: Path, *, previous_sha: str, backup: Path
) -> dict[str, str]:
    if os.name == "nt":
        raise RuntimeError("Automatic deployment rollback is supported only on the Linux VM")
    if re.fullmatch(r"[0-9a-f]{40}", previous_sha) is None:
        raise ValueError("previous_sha must be an exact 40-character Git SHA")
    database = database.resolve()
    backup = backup.resolve()
    if database != Path("/var/lib/nice-weather/nice-weather.sqlite3"):
        raise ValueError("Rollback database path is outside the canonical VM location")
    if not backup.is_file() or backup.parent != database.parent:
        raise ValueError("Rollback backup must be an existing file beside the canonical database")
    for unit in STOP_UNITS:
        _run(["systemctl", "stop", unit])
    failed_copy = database.with_name(
        f"nice-weather.failed-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
    )
    database.replace(failed_copy)
    shutil.copy2(backup, database)
    shutil.chown(database, user="nice-weather", group="nice-weather")
    _run(
        [
            "runuser",
            "-u",
            "nice-weather",
            "--",
            "git",
            "-C",
            "/opt/nice-weather/repo",
            "switch",
            "--detach",
            previous_sha,
        ]
    )
    _run(
        [
            "runuser",
            "-u",
            "nice-weather",
            "--",
            "/opt/nice-weather/.venv/bin/python",
            "-m",
            "pip",
            "install",
            "-e",
            "/opt/nice-weather/repo[collector]",
        ]
    )
    unit_directory = Path("/opt/nice-weather/repo/deploy/systemd")
    for unit in (
        "nice-weather-collector.service",
        "nice-weather-r2-sync.service",
        "nice-weather-r2-sync.timer",
        "nice-weather-dashboard.service",
        "nice-weather-runner.service",
    ):
        source = unit_directory / unit
        if source.is_file():
            shutil.copy2(source, Path("/etc/systemd/system") / unit)
    _run(["systemctl", "disable", "nice-weather-market-stream.service"])
    _run(["systemctl", "daemon-reload"])
    for service in (
        "nice-weather-collector.service",
        "nice-weather-r2-sync.timer",
        "nice-weather-dashboard.service",
        "nice-weather-runner.service",
    ):
        _run(["systemctl", "start", service])
    return {
        "status": "rolled_back",
        "restored_sha": previous_sha,
        "restored_backup": str(backup),
        "failed_database": str(failed_copy),
    }


def observe_deployment(
    database: str | Path,
    config: CityConfig,
    *,
    previous_sha: str,
    backup: str | Path,
    hours: float,
    interval_seconds: int,
    check_only: bool,
) -> dict[str, Any]:
    if hours <= 0 or interval_seconds < 10:
        raise ValueError("hours must be positive and interval_seconds must be at least 10")
    deadline = time.monotonic() + hours * 3600
    consecutive_failures = 0
    checks = 0
    latest: dict[str, Any] = {}
    while True:
        checks += 1
        latest = deployment_health(database, config)
        print(json.dumps(latest, sort_keys=True), flush=True)
        if latest["ok"]:
            consecutive_failures = 0
        else:
            consecutive_failures += 1
        hard_failure = "database_verification" in latest["failures"]
        if not check_only and (hard_failure or consecutive_failures >= 3):
            rollback = rollback_deployment(
                Path(database), previous_sha=previous_sha, backup=Path(backup)
            )
            return {"ok": False, "checks": checks, "last_check": latest, **rollback}
        if check_only or time.monotonic() >= deadline:
            return {"ok": latest["ok"], "checks": checks, "last_check": latest}
        time.sleep(interval_seconds)

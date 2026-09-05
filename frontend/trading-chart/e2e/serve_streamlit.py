from __future__ import annotations

import os
import sys
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.contract import parse_gamma_contract
from nice_weather.domain import (
    MarketTopTick,
    SettlementEvidence,
    SourceCapture,
    content_hash,
    stable_id,
)
from nice_weather.runner import run_fixture_once
from nice_weather.store import WeatherStore

ROOT = Path(__file__).resolve().parents[3]
DATABASE = ROOT / "tmp" / f"playwright-dashboard-{os.getpid()}.sqlite3"
MANIFEST = ROOT / "tests" / "fixtures" / "nyc_klga" / "2026-08-24T0043Z" / "manifest.json"
DASHBOARD = ROOT / "src" / "nice_weather" / "dashboard.py"


def source_capture(
    source: str,
    kind: str,
    received_at: datetime,
    local_day: datetime,
) -> SourceCapture:
    identity = f"playwright-{source}-{kind}"
    return SourceCapture(
        capture_id=stable_id("playwright-capture", identity),
        source=source,
        kind=kind,
        station_id="KLGA",
        requested_at=received_at - timedelta(seconds=1),
        received_at=received_at,
        local_date=local_day.date(),
        source_version="playwright-v1",
        content_hash=content_hash(identity),
        request_url="https://example.invalid/playwright",
        http_status=200,
        content_type="application/json",
        raw_bytes=identity.encode(),
        issued_at=received_at if kind == "forecast" else None,
        object_timezone="America/New_York",
    )


def add_chart_weather(database: Path, local_day: datetime) -> None:
    zone = ZoneInfo("America/New_York")
    start = local_day.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    received_at = start - timedelta(hours=1)
    forecast = source_capture("nws", "forecast", received_at, local_day)
    forecast_periods = [
        {
            "valid_at": start + timedelta(hours=index),
            "temperature_f": 60.0 + min(index, 7) + max(0, 14 - index) * 0.1,
            "object_timezone": "America/New_York",
        }
        for index in range(24)
    ]
    with WeatherStore(database) as store:
        store.save_source_capture(forecast, payload={}, forecast_periods=forecast_periods)
        for source, offset in (("aviationweather", 0.3), ("nws", -0.2)):
            capture = source_capture(source, "observation", received_at, local_day)
            observations = [
                {
                    "observed_at": start + timedelta(minutes=30 * index),
                    "temperature_f": 59.0 + min(index / 2, 7) + offset,
                    "temperature_c": None,
                    "raw_unit": "F",
                    "zone": zone,
                }
                for index in range(32)
            ]
            store.save_source_capture(capture, payload={}, observations=observations)
        settlement_capture = source_capture(
            "weather_gov", "settlement_page", received_at, local_day
        )
        store.save_source_capture(settlement_capture, payload={})
        rows = tuple((start + timedelta(hours=index), 60.0 + index) for index in range(8))
        store.save_settlement_evidence(
            SettlementEvidence(
                evidence_id=stable_id("playwright-evidence", local_day.date()),
                capture_id=settlement_capture.capture_id,
                station_id="KLGA",
                local_date=local_day.date(),
                received_at=received_at,
                table_text="Playwright running Tmax fixture",
                parse_status="parsed",
                tmax_f=67.0,
                page_url="https://www.weather.gov/wrh/timeseries?site=KLGA",
                object_timezone="America/New_York",
            ),
            rows,
        )


def prepare() -> tuple[dict[str, object], datetime]:
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    run_fixture_once(MANIFEST, DATABASE)
    with WeatherStore(DATABASE, read_only=True) as store:
        row = dict(
            store.connection.execute(
                """
                SELECT c.event_id,c.observation_start,c.timezone,b.condition_id,b.market_id,
                       b.bin_id,b.yes_token_id,b.label
                FROM contract_versions c JOIN contract_bins b USING(contract_version_id)
                ORDER BY b.ordinal LIMIT 1
                """
            ).fetchone()
        )
    local_day = datetime.combine(
        datetime.fromisoformat(str(row["observation_start"])).date(),
        datetime.min.time(),
        ZoneInfo(str(row["timezone"])),
    )
    add_chart_weather(DATABASE, local_day)
    return row, local_day.astimezone(UTC) + timedelta(hours=6)


def add_future_market() -> list[MarketTopTick]:
    now = datetime.now(UTC)
    config = load_city_config()
    bundle = load_fixture(MANIFEST, config)
    original = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    day = now.astimezone(config.zone).date() + timedelta(days=1)
    start = datetime.combine(day, datetime.min.time(), config.zone).astimezone(UTC)
    bins = tuple(replace(item, bin_id="future-" + item.bin_id) for item in original.bins)
    contract = replace(
        original,
        contract_version_id="future-contract",
        event_id="future-event",
        event_title="Future fixture NYC",
        local_day=day,
        bins=bins,
        observation_start=start,
        observation_end=start + timedelta(days=1),
    )
    snapshot = replace(bundle.gamma_snapshot, snapshot_id="future-gamma", received_at=now)
    capture = replace(
        source_capture("nws", "forecast", now, start),
        capture_id="future-forecast",
        content_hash=content_hash("future-forecast"),
    )
    future_ticks = []
    with WeatherStore(DATABASE) as store:
        store.save_discovered_contract(contract, snapshot)
        store.save_source_capture(
            capture,
            payload={},
            forecast_periods=[
                {
                    "valid_at": start + timedelta(hours=i),
                    "temperature_f": 70 + i / 4,
                    "object_timezone": config.timezone,
                }
                for i in range(25)
            ],
        )
        for item in bins:
            future_ticks.append(
                MarketTopTick(
                    tick_id="future-" + item.bin_id,
                    event_id=contract.event_id,
                    condition_id=item.condition_id,
                    market_id=item.market_id,
                    token_id=item.yes_token_id,
                    bin_id=item.bin_id,
                    label=item.label,
                    exchange_event_at=now,
                    received_at=now,
                    object_timezone=config.timezone,
                    object_local_date=now.astimezone(config.zone).date(),
                    source="clob_ws",
                    status="available",
                    event_hash=item.bin_id,
                    event_kind="snapshot",
                    best_bid=0.19,
                    best_ask=0.21,
                    mid=0.2,
                )
            )
            store.save_market_top_tick(future_ticks[-1])
    return future_ticks


def write_ticks(
    metadata: dict[str, object], start: datetime, future_ticks: list[MarketTopTick]
) -> None:
    index = 0
    while True:
        exchange_time = start + timedelta(minutes=index, milliseconds=250)
        mid = 0.20 + (index % 12) * 0.025
        tick = MarketTopTick(
            tick_id=stable_id("playwright-tick", index),
            event_id=str(metadata["event_id"]),
            condition_id=str(metadata["condition_id"]),
            market_id=str(metadata["market_id"]),
            bin_id=str(metadata["bin_id"]),
            token_id=str(metadata["yes_token_id"]),
            label=str(metadata["label"]),
            exchange_event_at=exchange_time.astimezone(UTC),
            received_at=exchange_time + timedelta(seconds=1),
            object_timezone=str(metadata["timezone"]),
            object_local_date=exchange_time.astimezone(UTC).date(),
            best_bid=mid - 0.01,
            best_ask=mid + 0.01,
            bid_size=100.0,
            ask_size=100.0,
            mid=mid,
            last_trade_price=None,
            source="clob_ws",
            event_kind="quote",
            status="available",
            event_hash=stable_id("playwright-event", index),
        )
        with WeatherStore(DATABASE) as store:
            store.save_market_top_tick(tick)
            now = datetime.now(UTC)
            for future_tick in future_ticks:
                store.save_market_top_tick(
                    replace(
                        future_tick,
                        tick_id=f"{future_tick.tick_id}-{index}",
                        event_hash=f"{future_tick.event_hash}-{index}",
                        exchange_event_at=now,
                        received_at=now,
                        event_kind="quote",
                    )
                )
        index += 1
        time.sleep(2)


def main() -> None:
    metadata, start = prepare()
    future_ticks = add_future_market()
    threading.Thread(target=write_ticks, args=(metadata, start, future_ticks), daemon=True).start()
    from streamlit.web import cli as streamlit_cli

    sys.argv = [
        "streamlit",
        "run",
        str(DASHBOARD),
        "--server.headless=true",
        "--server.address=127.0.0.1",
        "--server.port=8511",
        "--browser.gatherUsageStats=false",
        "--",
        "--db",
        str(DATABASE),
    ]
    raise SystemExit(streamlit_cli.main())


if __name__ == "__main__":
    main()

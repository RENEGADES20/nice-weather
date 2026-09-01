from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nice_weather.config import CityConfig
from nice_weather.domain import (
    ForecastPoint,
    OrderBook,
    PriceLevel,
    RawSnapshot,
    WeatherObservation,
    content_hash,
    stable_id,
)


@dataclass(frozen=True)
class FixtureBundle:
    manifest: dict[str, Any]
    decision_time: datetime
    snapshots: tuple[RawSnapshot, ...]
    gamma_snapshot: RawSnapshot
    books: dict[str, OrderBook]
    observations: tuple[WeatherObservation, ...]
    forecasts: tuple[ForecastPoint, ...]
    extra_input_snapshot_ids: tuple[str, ...] = ()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Fixture timestamp is naive: {value}")
    return parsed


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def verify_manifest(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    manifest = _read_json(path)
    for filename, expected in manifest["sha256"].items():
        actual = hashlib.sha256((path.parent / filename).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Fixture hash mismatch for {filename}: {actual} != {expected}")
    return manifest


def _snapshot(
    source: str, kind: str, payload: Any, capture: dict[str, Any], **ids: str
) -> RawSnapshot:
    payload_hash = content_hash(payload)
    return RawSnapshot(
        snapshot_id=stable_id("snapshot", source, kind, payload_hash, capture["received_at"]),
        source=source,
        kind=kind,
        received_at=_parse_time(capture["received_at"]),
        source_version=payload_hash,
        payload=payload,
        request_url=capture.get("url")
        or capture.get("requested_url")
        or capture.get("url_template"),
        http_status=int(capture.get("http_status", 200)),
        **ids,
    )


def load_fixture(manifest_path: str | Path, config: CityConfig) -> FixtureBundle:
    path = Path(manifest_path).resolve()
    manifest = verify_manifest(path)
    base = path.parent
    gamma_raw = _read_json(base / manifest["files"]["gamma_event"])
    books_raw = _read_json(base / manifest["files"]["clob_books"])
    metar_raw = _read_json(base / manifest["files"]["aviationweather_metar"])
    points_raw = _read_json(base / manifest["files"]["nws_points"])
    hourly_raw = _read_json(base / manifest["files"]["nws_hourly"])
    decision_time = _parse_time(manifest["decision_time"])

    gamma_payload = {"events": gamma_raw["events"]}
    gamma_snapshot = _snapshot(
        "polymarket_gamma",
        "event",
        gamma_payload,
        gamma_raw["capture"],
        event_id=str(gamma_raw["events"][0]["id"]),
    )
    snapshots: list[RawSnapshot] = [gamma_snapshot]
    books: dict[str, OrderBook] = {}
    for raw_book in books_raw["books"]:
        snapshot = _snapshot(
            "polymarket_clob",
            "order_book",
            raw_book,
            books_raw["capture"],
            market_id=str(raw_book["market"]),
            token_id=str(raw_book["asset_id"]),
        )
        snapshots.append(snapshot)
        exchange_time = datetime.fromtimestamp(int(raw_book["timestamp"]) / 1000, tz=UTC)
        books[str(raw_book["asset_id"])] = OrderBook(
            snapshot_id=snapshot.snapshot_id,
            book_hash=str(raw_book["hash"]),
            token_id=str(raw_book["asset_id"]),
            market_id=str(raw_book["market"]),
            exchange_time=exchange_time,
            received_at=snapshot.received_at,
            bids=tuple(
                sorted(
                    (
                        PriceLevel(float(level["price"]), float(level["size"]))
                        for level in raw_book["bids"]
                    ),
                    key=lambda level: level.price,
                    reverse=True,
                )
            ),
            asks=tuple(
                sorted(
                    (
                        PriceLevel(float(level["price"]), float(level["size"]))
                        for level in raw_book["asks"]
                    ),
                    key=lambda level: level.price,
                )
            ),
        )

    metar_payload = {"observations": metar_raw["observations"]}
    metar_snapshot = _snapshot(
        "aviationweather",
        "metar",
        metar_payload,
        metar_raw["capture"],
    )
    snapshots.append(metar_snapshot)
    observations = tuple(
        WeatherObservation(
            snapshot_id=metar_snapshot.snapshot_id,
            station_id=config.station_id,
            observed_at=_parse_time(item["reportTime"]),
            received_at=metar_snapshot.received_at,
            temperature_f=float(item["temp"]) * 9.0 / 5.0 + 32.0,
        )
        for item in metar_raw["observations"]
    )

    points_payload = {key: value for key, value in points_raw.items() if key != "capture"}
    points_snapshot = _snapshot("nws", "points", points_payload, points_raw["capture"])
    snapshots.append(points_snapshot)
    hourly_payload = {"properties": hourly_raw["properties"]}
    hourly_snapshot = _snapshot("nws", "hourly_forecast", hourly_payload, hourly_raw["capture"])
    snapshots.append(hourly_snapshot)
    issued_at = _parse_time(hourly_raw["properties"]["generatedAt"])
    forecasts = tuple(
        ForecastPoint(
            snapshot_id=hourly_snapshot.snapshot_id,
            source="nws_hourly",
            issued_at=issued_at,
            valid_at=_parse_time(item["startTime"]),
            received_at=hourly_snapshot.received_at,
            temperature_f=float(item["temperature"]),
        )
        for item in hourly_raw["properties"]["periods"]
        if item["temperatureUnit"] == "F"
    )
    if any(not snapshot.available_at(decision_time) for snapshot in snapshots):
        raise ValueError("Fixture contains a snapshot received after decision_time")
    return FixtureBundle(
        manifest=manifest,
        decision_time=decision_time,
        snapshots=tuple(snapshots),
        gamma_snapshot=gamma_snapshot,
        books=books,
        observations=observations,
        forecasts=forecasts,
    )

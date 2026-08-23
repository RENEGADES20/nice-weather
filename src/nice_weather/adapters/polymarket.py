from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from nice_weather.config import CityConfig
from nice_weather.domain import RawSnapshot, content_hash, stable_id, utc_now


class PolymarketReadOnlyAdapter:
    gamma_url = "https://gamma-api.polymarket.com"
    clob_url = "https://clob.polymarket.com"

    def __init__(self, timeout: float = 15.0) -> None:
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PolymarketReadOnlyAdapter:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def discover(self, config: CityConfig, decision_time: datetime) -> RawSnapshot:
        query = "highest temperature in New York"
        search_url = f"{self.gamma_url}/public-search"
        response = self.client.get(
            search_url,
            params={"q": query, "events_status": "active", "limit_per_type": 50},
        )
        response.raise_for_status()
        payload = response.json()
        candidates = [
            event
            for event in payload.get("events", [])
            if event.get("active")
            and not event.get("closed")
            and "highest temperature" in str(event.get("title", "")).lower()
            and "nyc" in str(event.get("title", "")).lower()
            and "klga" in str(event.get("description", "")).lower()
        ]
        local_day = decision_time.astimezone(config.zone).date().isoformat()
        candidates = [event for event in candidates if str(event.get("eventDate", "")) >= local_day]
        if not candidates:
            event_payload: dict[str, Any] = {"events": []}
        else:
            candidates.sort(key=lambda event: (str(event.get("eventDate", "")), str(event["id"])))
            closest_day = str(candidates[0].get("eventDate", ""))
            closest = [
                event for event in candidates if str(event.get("eventDate", "")) == closest_day
            ]
            if len(closest) != 1:
                received_at = utc_now()
                ambiguous_payload = {"events": closest, "selection_ambiguous": True}
                return RawSnapshot(
                    snapshot_id=stable_id("snapshot", "polymarket_gamma", "ambiguous", received_at),
                    source="polymarket_gamma",
                    kind="event",
                    received_at=received_at,
                    source_version=content_hash(ambiguous_payload),
                    payload=ambiguous_payload,
                    request_url=str(response.request.url),
                    http_status=response.status_code,
                )
            target = closest[0]
            detail = self.client.get(f"{self.gamma_url}/events", params={"slug": target["slug"]})
            detail.raise_for_status()
            event_payload = {"events": detail.json()}
        payload_hash = content_hash(event_payload)
        received_at = utc_now()
        return RawSnapshot(
            snapshot_id=stable_id(
                "snapshot", "polymarket_gamma", "event", payload_hash, received_at
            ),
            source="polymarket_gamma",
            kind="event",
            received_at=received_at,
            source_version=payload_hash,
            payload=event_payload,
            request_url=str(response.request.url),
            http_status=response.status_code,
        )

    def fetch_books(self, token_ids: list[str], decision_time: datetime) -> list[RawSnapshot]:
        snapshots: list[RawSnapshot] = []
        for token_id in token_ids:
            response = self.client.get(f"{self.clob_url}/book", params={"token_id": token_id})
            response.raise_for_status()
            payload = response.json()
            payload_hash = content_hash(payload)
            received_at = utc_now()
            snapshots.append(
                RawSnapshot(
                    snapshot_id=stable_id("snapshot", "polymarket_clob", token_id, payload_hash),
                    source="polymarket_clob",
                    kind="order_book",
                    received_at=received_at,
                    source_version=str(payload.get("hash", payload_hash)),
                    payload=payload,
                    market_id=str(payload.get("market", "")),
                    token_id=token_id,
                    request_url=str(response.request.url),
                    http_status=response.status_code,
                )
            )
        return snapshots

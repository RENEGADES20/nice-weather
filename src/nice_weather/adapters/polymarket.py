from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import httpx

from nice_weather.config import CityConfig
from nice_weather.domain import RawSnapshot, content_hash, stable_id, utc_now


class MarketDataRequestError(RuntimeError):
    def __init__(
        self,
        *,
        stage: str,
        url: str,
        attempts: int,
        elapsed_ms: int,
        cause: Exception,
    ) -> None:
        self.context = {
            "stage": stage,
            "url": url,
            "attempts": attempts,
            "elapsed_ms": elapsed_ms,
            "cause_type": type(cause).__name__,
        }
        super().__init__(
            f"{stage} failed after {attempts} attempt(s) in {elapsed_ms} ms: {cause}"
        )


class PolymarketReadOnlyAdapter:
    gamma_url = "https://gamma-api.polymarket.com"
    clob_url = "https://clob.polymarket.com"

    def __init__(self, timeout: float = 15.0, retries: int = 2) -> None:
        if retries < 1:
            raise ValueError("retries must be at least 1")
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)
        self.retries = retries

    def _get(
        self, url: str, *, stage: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        started = time.monotonic()
        for attempt in range(1, self.retries + 1):
            try:
                response = self.client.get(url, params=params)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                raise MarketDataRequestError(
                    stage=stage,
                    url=url,
                    attempts=attempt,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    cause=exc,
                ) from exc
            except httpx.TransportError as exc:
                if attempt < self.retries:
                    continue
                raise MarketDataRequestError(
                    stage=stage,
                    url=url,
                    attempts=attempt,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    cause=exc,
                ) from exc
        raise AssertionError("unreachable")

    def _post(self, url: str, *, stage: str, payload: Any) -> httpx.Response:
        started = time.monotonic()
        for attempt in range(1, self.retries + 1):
            try:
                response = self.client.post(url, json=payload)
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as exc:
                raise MarketDataRequestError(
                    stage=stage,
                    url=url,
                    attempts=attempt,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    cause=exc,
                ) from exc
            except httpx.TransportError as exc:
                if attempt < self.retries:
                    continue
                raise MarketDataRequestError(
                    stage=stage,
                    url=url,
                    attempts=attempt,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    cause=exc,
                ) from exc
        raise AssertionError("unreachable")

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> PolymarketReadOnlyAdapter:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def discover(self, config: CityConfig, decision_time: datetime) -> RawSnapshot:
        query = "highest temperature in New York"
        search_url = f"{self.gamma_url}/public-search"
        response = self._get(
            search_url,
            stage="gamma_search",
            params={"q": query, "events_status": "active", "limit_per_type": 50},
        )
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
                    snapshot_id=stable_id(
                        "snapshot", "polymarket_gamma", "ambiguous", content_hash(ambiguous_payload)
                    ),
                    source="polymarket_gamma",
                    kind="event",
                    received_at=received_at,
                    source_version=content_hash(ambiguous_payload),
                    payload=ambiguous_payload,
                    request_url=str(response.request.url),
                    http_status=response.status_code,
                    requested_at=decision_time,
                )
            target = closest[0]
            detail = self._get(
                f"{self.gamma_url}/events",
                stage="gamma_event_detail",
                params={"slug": target["slug"]},
            )
            event_payload = {"events": detail.json()}
        payload_hash = content_hash(event_payload)
        received_at = utc_now()
        return RawSnapshot(
            snapshot_id=stable_id(
                "snapshot", "polymarket_gamma", "event", payload_hash
            ),
            source="polymarket_gamma",
            kind="event",
            received_at=received_at,
            source_version=payload_hash,
            payload=event_payload,
            request_url=str(response.request.url),
            http_status=response.status_code,
            requested_at=decision_time,
        )

    def fetch_books(self, token_ids: list[str], decision_time: datetime) -> list[RawSnapshot]:
        snapshots: list[RawSnapshot] = []
        for token_id in token_ids:
            response = self._get(
                f"{self.clob_url}/book",
                stage="clob_book",
                params={"token_id": token_id},
            )
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
                    requested_at=decision_time,
                )
            )
        return snapshots

    def fetch_candidate_quotes(
        self, token_ids: list[str], decision_time: datetime
    ) -> list[RawSnapshot]:
        return self.fetch_books(token_ids, decision_time)

    def fetch_books_batch_payload(self, token_ids: list[str]) -> list[dict[str, Any]]:
        if not token_ids:
            return []
        response = self._post(
            f"{self.clob_url}/books",
            stage="clob_books_batch",
            payload=[{"token_id": token_id} for token_id in token_ids],
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("CLOB batch books response must be a JSON list")
        return [item for item in payload if isinstance(item, dict)]

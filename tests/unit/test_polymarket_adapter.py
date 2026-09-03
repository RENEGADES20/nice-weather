from datetime import UTC, datetime

import httpx
import pytest

from nice_weather.adapters.polymarket import MarketDataRequestError, PolymarketReadOnlyAdapter
from nice_weather.config import load_city_config


def test_market_request_retries_and_reports_stage() -> None:
    adapter = PolymarketReadOnlyAdapter(timeout=0.01, retries=2)
    calls = 0

    def fail_get(_url, *, params=None):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow")

    adapter.client.get = fail_get
    try:
        with pytest.raises(MarketDataRequestError) as captured:
            adapter._get("https://gamma-api.polymarket.com/public-search", stage="gamma_search")
    finally:
        adapter.close()

    assert calls == 2
    assert captured.value.context["stage"] == "gamma_search"
    assert captured.value.context["cause_type"] == "ReadTimeout"


def test_gamma_snapshot_id_is_content_addressed(monkeypatch) -> None:
    adapter = PolymarketReadOnlyAdapter()
    config = load_city_config()
    search = httpx.Response(
        200,
        json={
            "events": [
                {
                    "id": "event",
                    "slug": "nyc-temperature",
                    "title": "Highest temperature in NYC",
                    "description": "KLGA",
                    "eventDate": "2026-09-03",
                    "active": True,
                    "closed": False,
                }
            ]
        },
        request=httpx.Request("GET", "https://gamma-api.polymarket.com/public-search"),
    )
    detail = httpx.Response(
        200,
        json=[{"id": "event", "slug": "nyc-temperature", "markets": []}],
        request=httpx.Request("GET", "https://gamma-api.polymarket.com/events"),
    )
    responses = iter((search, detail, search, detail))
    monkeypatch.setattr(adapter, "_get", lambda *_args, **_kwargs: next(responses))
    try:
        first = adapter.discover(config, datetime(2026, 9, 2, 12, 0, tzinfo=UTC))
        second = adapter.discover(config, datetime(2026, 9, 2, 12, 1, tzinfo=UTC))
    finally:
        adapter.close()

    assert first.snapshot_id == second.snapshot_id

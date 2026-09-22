"""Public listed-day metadata discovery; no accounts and no book subscriptions."""

from __future__ import annotations

import calendar
import re
from datetime import date
from urllib.parse import urlencode

import httpx

from nice_weather.trading.us_markets import KALSHI, POLY_US, event_url, normalize


def event_day(venue, event):
    if venue == "poly_us":
        match = re.fullmatch(r"temp-nychigh-(\d{4}-\d{2}-\d{2})", event.get("slug", ""))
        return date.fromisoformat(match[1]).isoformat() if match else None
    match = re.fullmatch(r"KXHIGHNY-(\d{2})([A-Z]{3})(\d{2})", event.get("event_ticker", ""))
    if not match:
        return None
    months = {name.upper(): i for i, name in enumerate(calendar.month_abbr) if name}
    return date(2000 + int(match[1]), months[match[2]], int(match[3])).isoformat()


async def discover(client, venue, today, fetch, series=None):
    """fetch(url) returns (body, received, capture_id); injected for capture and tests."""
    cursor, offset, seen = "", 0, set()
    found = {}
    while True:
        params = ({"series_ticker": "KXHIGHNY", "status": "open", "limit": 200,
                   "cursor": cursor} if venue == "kalshi" else
                  {"active": "true", "closed": "false", "limit": 100, "offset": offset})
        url = (KALSHI if venue == "kalshi" else POLY_US) + "/events?" + urlencode(params)
        payload, _, _ = await fetch(url)
        events = payload["events"]
        ids = tuple(e.get("event_ticker", e.get("slug", e.get("id"))) for e in events)
        if events and ids in seen:
            raise ValueError("Public event pagination repeated a page")
        seen.add(ids)
        for event in events:
            day = event_day(venue, event)
            if day and day >= today and day not in found:
                body, received, capture = await fetch(event_url(venue, day))
                found[day] = [c | {"capture_id": capture, "source_url": event_url(venue, day)}
                              for c in normalize(venue, day, body, received, series)]
        if venue == "kalshi":
            next_cursor = payload.get("cursor")
            if next_cursor and next_cursor == cursor:
                raise ValueError("Public event pagination cursor did not advance")
            cursor = next_cursor
            if not cursor:
                break
        else:
            if len(events) < 100:
                break
            offset += len(events)
    return found


async def refresh_directory(client, store, venue, today, series):
    from nice_weather.trading.feed import capture_json

    async def fetch(url):
        return await capture_json(client, store, venue, url)

    try:
        directory = await discover(client, venue, today, fetch, series)
        for day, contracts in directory.items():
            store.publish("market_directory", f"{venue}:{day}", contracts)
        store.publish("health", f"{venue}_directory", {"status": "connected"})
    except (httpx.HTTPError, KeyError, ValueError, TypeError, OSError):
        store.publish("health", f"{venue}_directory", {
            "status": "unavailable", "message": "Listed-day discovery failed"})

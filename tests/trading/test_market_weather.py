from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from nice_weather.trading.api import create_app
from nice_weather.trading.feed import FeedStore
from nice_weather.trading.market_discovery import discover
from nice_weather.trading.market_weather import catalog, market_day, scoped_history, weather_history
from nice_weather.trading.storage import connect


def contract(venue, day, token="bin"):
    return {"venue": venue, "station_id": "KNYC", "local_day": day,
            "yes_token_id": venue + ":" + day + ":" + token, "lower": None, "upper": 70,
            "title": "70 or below", "received_at": 1, "active": True,
            "observation_start": day + "T05:00:00Z",
            "observation_end": datetime.fromtimestamp(
                datetime.fromisoformat(day + "T05:00:00+00:00").timestamp() + 86400,
                UTC).isoformat()}


def test_catalog_days_isolation_and_legacy_history(tmp_path):
    feed = FeedStore(tmp_path / "feed.sqlite3")
    for venue in ("kalshi", "poly_us"):
        for day in ("2026-09-19", "2026-09-20"):
            feed.publish("contracts", venue, [contract(venue, day)], 1)
        feed.publish("market_directory", venue + ":2026-09-23",
                     [contract(venue, "2026-09-23")], 2)
    feed.publish("book", "kalshi:2026-09-23:bin", {
        "bids": [[0.4, 1]], "asks": [[0.5, 1]], "received_at": 2}, 2)
    assert [d["day"] for d in catalog(feed.path, "kalshi")["days"]] == [
        "2026-09-19", "2026-09-20", "2026-09-23"]
    assert market_day(feed.path, "kalshi", "2026-09-21")["reason"] == "NO_CONTRACTS"
    assert scoped_history(feed, "kalshi", "2026-09-23", "kalshi:2026-09-23:bin")[
        "points"][0]["time"] == 2  # Pre-market-day quotes are retained.
    with pytest.raises(ValueError, match="belong"):
        scoped_history(feed, "poly_us", "2026-09-23", "kalshi:2026-09-23:bin")
    # A later contract snapshot replaces that day's bins without deleting earlier days.
    feed.publish("contracts", "kalshi", [contract("kalshi", "2026-09-20", "new")], 3)
    assert len(market_day(feed.path, "kalshi", "2026-09-20")["contracts"]) == 1
    # The maintained projection must not touch repeated historical contract blobs.
    with connect(feed.path) as con:
        con.execute("UPDATE feed_events SET body='invalid' WHERE kind='contracts'")
    assert len(catalog(feed.path, "kalshi")["days"]) == 3


def test_catalog_without_legacy_projection(tmp_path):
    feed = FeedStore(tmp_path / "feed.sqlite3")
    for day in ("2026-09-19", "2026-09-20"):
        feed.publish("contracts", "kalshi", [contract("kalshi", day)])
    with connect(feed.path) as con:
        con.execute("DROP TABLE settlement_watch")
    assert [d["day"] for d in catalog(feed.path, "kalshi")["days"]] == [
        "2026-09-19", "2026-09-20"]


@pytest.mark.parametrize("day", ["2026-03-08", "2026-11-01"])
def test_weather_revisions_station_cli_and_climate_window(tmp_path, day):
    feed = FeedStore(tmp_path / "feed.sqlite3")
    c = contract("kalshi", day)
    feed.publish("contracts", "kalshi", [c])
    start = datetime.fromisoformat(c["observation_start"].replace("Z", "+00:00")).timestamp()
    def metar(temp, raw, received, station="KNYC"):
        feed.publish("weather", "metar", {"station": "KNYC", "data": [
            {"icaoId": station, "temp": temp, "obsTime": start + 60, "rawOb": raw}]}, received)
    metar(20, "v1", start + 120)
    metar(20, "v1", start + 240)
    metar(21, "v2", start + 300)
    metar(90, "foreign", start + 360, "KLGA")
    feed.publish("weather", "hrrr", {"station": "KNYC", "cycle": start - 3600,
        "points": [{"valid_at": start + 3600, "temperature_f": 70,
                    "received_at": start - 1200}]}, start - 1000)
    feed.publish("weather", "cli", {"station": "KNYC", "text":
        "...THE CENTRAL PARK NY CLIMATE SUMMARY FOR SEPTEMBER 19 2026..."}, start)
    result = weather_history(feed.path, "kalshi", day)
    assert result["end"] - result["start"] == 86400
    assert [p["value"] for p in result["sources"]["metar"]] == pytest.approx([68, 69.8])
    assert [p["received_at"] for p in result["sources"]["metar"]] == [start + 120, start + 300]
    assert result["sources"]["hrrr"][0]["received_at"] == start - 1000
    assert result["cli"] == []
    assert "hourly_temp" in result["missing_sources"]


def test_api_scopes_history_and_keeps_old_response(tmp_path):
    app = create_app(tmp_path, password="test", origin="http://testserver")
    feed = FeedStore(tmp_path / "feed.sqlite3")
    c = contract("kalshi", "2026-09-19")
    feed.publish("contracts", "kalshi", [c])
    client = TestClient(app)
    assert client.get("/api/markets?venue=kalshi").status_code == 401
    client.post("/api/login", json={"password": "test"}, headers={"origin": "http://testserver"})
    assert client.get("/api/markets?venue=kalshi").json()["days"][0]["day"] == "2026-09-19"
    assert client.get("/api/history", params={"token": c["yes_token_id"]}).json() == []
    response = client.get("/api/history", params={"token": c["yes_token_id"],
                          "venue": "kalshi", "day": "2026-09-19"})
    assert response.json()["reason"] == "NO_PRICE_HISTORY"
    assert client.get("/api/history?token=x&venue=kalshi").status_code == 400
    assert client.get("/api/market-day?venue=poly_intl&day=2026-09-19").status_code == 400
    assert client.get("/api/weather-history?venue=kalshi&day=invalid").status_code == 400
    response = client.get("/api/market-quote?venue=kalshi&day=2026-09-19&token=wrong")
    assert response.status_code == 400


def test_discovery_paginates_and_keeps_only_knyc(monkeypatch):
    calls = []
    async def fetch(url):
        calls.append(url)
        if "cursor=next" in url:
            return {"events": [{"event_ticker": "KXHIGHNY-26SEP24"}], "cursor": ""}, 2, 1
        if "/events?" in url:
            return {"events": [{"event_ticker": "KXHIGHNY-26SEP23"},
                               {"event_ticker": "KXHIGHLA-26SEP23"}], "cursor": "next"}, 1, 1
        return {}, 2, 1
    monkeypatch.setattr("nice_weather.trading.market_discovery.normalize",
                        lambda venue, day, *_: [contract(venue, day)])
    result = asyncio.run(discover(None, "kalshi", "2026-09-22", fetch))
    assert list(result) == ["2026-09-23", "2026-09-24"]
    assert len(calls) == 4


def test_observation_reversion_keeps_later_receipt(tmp_path):
    feed = FeedStore(tmp_path / "feed.sqlite3")
    day = "2026-09-19"
    start = datetime(2026, 9, 19, 5, tzinfo=UTC).timestamp()
    for offset, value, version in [(60, 20, "a"), (120, 21, "b"), (180, 20, "a"), (240, 20, "a")]:
        feed.publish("weather", "metar", {"station": "KNYC", "data": [{
            "icaoId": "KNYC", "obsTime": start, "temp": value, "revision_id": version,
            "first_received_at": start + (60 if version == "a" else 120)}]}, start + offset)
    points = weather_history(feed.path, "kalshi", day)["sources"]["metar"]
    assert [p["received_at"] for p in points] == [start + 60, start + 120, start + 180]
    assert [p["value"] for p in points] == pytest.approx([68, 69.8, 68])

from __future__ import annotations

import asyncio
import gzip
import json
import sqlite3
import time
from datetime import datetime

import httpx
import pytest

from nice_weather.trading.feed import (
    FeedStore,
    capture_book,
    capture_json,
    prepare_probability_history,
)
from nice_weather.trading.market_weather import scoped_history
from nice_weather.trading.storage import connect
from nice_weather.trading.us_markets import kalshi_trade_probability, normalize_book

STAMP = "2026-09-27T04:00:00.123456789Z"
SOURCE_TIME = datetime.fromisoformat(STAMP).timestamp()
RECEIVED = SOURCE_TIME + 2


def test_poly_display_probability_ignores_missing_book_side_and_midpoint():
    payload = {"marketData": {"bids": [], "offers": [
        {"px": {"value": "0.90"}, "qty": "1"}], "stats": {
        "lastPriceSample": {"longPx": {"value": "0.12"}, "ts": STAMP},
        "lastTradePx": {"value": "0.30"}, "lastTradeSetTime": STAMP}}}
    quote = normalize_book("poly_us", payload, RECEIVED)
    assert quote["probability"] == 0.12
    assert quote["probability_source"] == "poly_us_display_price"
    assert quote["probability_time"] == SOURCE_TIME
    assert quote["probability_received_at"] == RECEIVED
    assert quote["bids"] == [] and quote["asks"] == [[0.9, 1]]
    del payload["marketData"]["stats"]["lastPriceSample"]
    assert normalize_book("poly_us", payload, RECEIVED)["probability"] == 0.30
    payload["marketData"]["stats"] = {}
    assert normalize_book("poly_us", payload, RECEIVED)["probability"] is None


@pytest.mark.parametrize("value", [0, 1, "0.25", None, True, -0.1, 1.1, "NaN", "Infinity"])
def test_probability_range_and_absence(value):
    quote = normalize_book("poly_us", {"marketData": {"stats": {
        "lastPriceSample": {"longPx": {"value": value}, "ts": STAMP}}}}, RECEIVED)
    expected = float(value) if value in (0, 1, "0.25") and value is not True else None
    assert quote["probability"] == expected
    quote = normalize_book("poly_us", {"marketData": {"stats": {
        "lastPriceSample": {"longPx": {"value": 0.5}, "ts": STAMP}}}}, SOURCE_TIME - 1)
    assert quote["probability"] is None  # Reject data from after its actual receipt.


def test_kalshi_uses_yes_trade_and_preserves_trade_time():
    payload = {"trades": [{"ticker": "KXHIGHNY-26SEP27-T72", "count_fp": "1.0",
        "yes_price_dollars": "0.21", "no_price_dollars": "0.79", "created_time": STAMP}]}
    quote = kalshi_trade_probability(payload, RECEIVED, "KXHIGHNY-26SEP27-T72")
    assert quote == {"probability": 0.21, "probability_source": "kalshi_last_trade",
                     "probability_time": SOURCE_TIME, "probability_received_at": RECEIVED}
    assert kalshi_trade_probability(payload, RECEIVED, "other")["probability"] is None
    payload["trades"][0]["count_fp"] = "NaN"
    assert kalshi_trade_probability(payload, RECEIVED, "KXHIGHNY-26SEP27-T72")[
        "probability"] is None
    assert normalize_book("kalshi", {"orderbook_fp": {
        "yes_dollars": [["0.10", "1"]]}}, RECEIVED)["probability"] is None


def test_scoped_history_keeps_legacy_gap_and_probability_provenance(tmp_path):
    feed = FeedStore(tmp_path / "feed.sqlite3")
    token = "kalshi:KXHIGHNY-26SEP27-T72"
    feed.publish("contracts", "kalshi", [{"venue": "kalshi", "station_id": "KNYC",
        "local_day": "2026-09-27", "yes_token_id": token}], RECEIVED)
    feed.publish("book", token, {"bids": [[0.1, 1]], "asks": [[0.9, 1]]}, RECEIVED)
    feed.publish("book", token, {"bids": [], "asks": [], "received_at": RECEIVED,
        "probability": 0, "probability_source": "kalshi_last_trade",
        "probability_time": SOURCE_TIME, "probability_received_at": RECEIVED + 1,
        "capture_id": 3, "probability_capture_id": 4}, RECEIVED + 1)
    points = scoped_history(feed, "kalshi", "2026-09-27", token)["points"]
    assert points[0]["probability"] is None
    assert points[1]["probability"] == 0
    assert points[1]["time"] == RECEIVED + 1
    assert points[1]["probability_time"] == SOURCE_TIME
    assert points[1]["received_at"] == RECEIVED
    assert points[1]["probability_received_at"] == RECEIVED + 1
    assert points[1]["probability_capture_id"] == 4


def test_capture_keeps_book_when_trade_request_fails(tmp_path):
    store = FeedStore(tmp_path / "feed.sqlite3")
    def transport(request):
        if request.url.path.endswith("/trades"):
            return httpx.Response(503, json={"error": "unavailable"})
        return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [["0.1", "3"]]}})
    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await capture_book(client, store, {"venue": "kalshi",
                "condition_id": "KXHIGHNY-26SEP27-T72"})
    quote = asyncio.run(fetch())
    assert quote["bids"] == [[0.1, 3]]
    assert quote["probability"] is None
    with connect(store.path, readonly=True) as con:
        assert con.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 2


def test_legacy_poly_probability_uses_exact_captured_body_and_receipt(tmp_path):
    store = FeedStore(tmp_path / "feed.sqlite3")
    slug = "tc-temp-nychigh-2026-09-27-lt65f"
    contract = {"venue": "poly_us", "condition_id": slug}
    url = f"https://gateway.polymarket.us/v1/markets/{slug}/book"
    payload = {"marketData": {"marketSlug": slug, "stats": {
        "lastPriceSample": {"longPx": {"value": 0.31}, "ts": STAMP}}}}
    capture = store.capture("poly_us", url, RECEIVED - 1, RECEIVED,
                            json.dumps(payload).encode())
    with connect(store.path) as con:
        # Model a response captured before market body retention was disabled.
        body_hash = con.execute("SELECT hash FROM captures WHERE id=?", (capture,)).fetchone()[0]
        con.execute("INSERT INTO capture_bodies VALUES (?,?)",
                    (body_hash, gzip.compress(json.dumps(payload).encode())))
    payload["marketData"]["stats"]["lastPriceSample"]["longPx"]["value"] = 0.85
    store.capture("poly_us", url.replace(slug, slug + "-other"), RECEIVED - 1, RECEIVED,
                  json.dumps(payload).encode())
    points = [{"time": RECEIVED, "probability": None},
              {"time": RECEIVED + 1, "probability": None}]
    restored = store.restore_probabilities(points, contract)
    assert restored[0]["probability"] == 0.31
    assert restored[0]["probability_time"] == SOURCE_TIME
    assert restored[0]["probability_received_at"] == RECEIVED
    assert restored[0]["probability_capture_id"] == capture
    assert restored[1]["probability"] is None  # Never nearest-match a different receipt.
    with connect(store.path) as con:
        con.execute("UPDATE capture_bodies SET body=X''")
    assert store.restore_probabilities([{"time": RECEIVED}], contract) == [{"time": RECEIVED}]


def test_slow_trade_request_has_its_own_budget_and_keeps_received_book(tmp_path):
    store = FeedStore(tmp_path / "feed.sqlite3")
    starts = []
    async def transport(request):
        starts.append((request.url.path, time.monotonic()))
        if request.url.path.endswith("/trades"):
            await asyncio.sleep(10)
            return httpx.Response(200, json={"trades": []})
        await asyncio.sleep(0.03)
        return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [["0.1", "3"]]}})
    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await capture_book(client, store, {"venue": "kalshi",
                "condition_id": "KXHIGHNY-26SEP27-T72"})
    started = time.monotonic()
    quote = asyncio.run(fetch())
    assert time.monotonic() - started < 2
    assert abs(starts[0][1] - starts[1][1]) < 0.02
    assert quote["bids"] == [[0.1, 3]] and quote["probability"] is None
    assert quote["time"] == quote["received_at"]


def test_parallel_trade_receipt_remains_distinct_from_later_book(tmp_path):
    store = FeedStore(tmp_path / "feed.sqlite3")
    async def transport(request):
        if request.url.path.endswith("/trades"):
            return httpx.Response(200, json={"trades": [{"ticker": "KXHIGHNY-26SEP27-T72",
                "count_fp": "1", "yes_price_dollars": "0.21", "created_time": STAMP}]})
        await asyncio.sleep(0.03)
        return httpx.Response(200, json={"orderbook_fp": {"yes_dollars": [["0.1", "3"]]}})
    async def fetch():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await capture_book(client, store, {"venue": "kalshi",
                "condition_id": "KXHIGHNY-26SEP27-T72"})
    quote = asyncio.run(fetch())
    assert quote["probability"] == 0.21
    assert quote["probability_received_at"] < quote["received_at"] == quote["time"]
    assert quote["probability_time"] == SOURCE_TIME


def test_capture_write_does_not_block_event_loop_or_change_http_receipt():
    class SlowStore:
        def capture(self, source, url, requested, received, body):
            time.sleep(0.15)
            return 1
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": True}))) as client:
            task = asyncio.create_task(capture_json(client, SlowStore(), "test", "https://test"))
            await asyncio.sleep(0.03)
            assert not task.done()  # The loop ran while the database write was sleeping.
            payload, received, capture = await task
            assert time.time() - received >= 0.15
            assert payload == {"ok": True} and capture == 1
    asyncio.run(run())


def test_probability_lookup_backfill_resumes_without_skipping_old_captures(tmp_path):
    store = FeedStore(tmp_path / "feed.sqlite3")
    slug = "tc-temp-nychigh-2026-09-27-lt65f"
    contract = {"venue": "poly_us", "condition_id": slug}
    url = f"https://gateway.polymarket.us/v1/markets/{slug}/book"
    payload = json.dumps({"marketData": {"marketSlug": slug, "stats": {
        "lastPriceSample": {"longPx": {"value": 0.31}, "ts": STAMP}}}}).encode()
    store.capture("poly_us", url, RECEIVED - 1, RECEIVED, payload)
    with connect(store.path) as con:
        body_hash = con.execute("SELECT hash FROM captures WHERE id=1").fetchone()[0]
        con.execute("INSERT INTO capture_bodies VALUES (?,?)", (body_hash, gzip.compress(payload)))
        con.executemany("INSERT INTO captures VALUES (?, 'poly_us', ?, ?, ?, ?)", [
            (i, url, RECEIVED + i - 1, RECEIVED + i, body_hash) for i in range(2, 2002)])
        con.execute("UPDATE probability_chart_progress SET capture_id=0")
        con.execute("CREATE TRIGGER stop_probability_backfill "
                    "BEFORE INSERT ON probability_capture_lookup WHEN NEW.capture_id=2001 "
                    "BEGIN SELECT RAISE(ABORT, 'interrupted'); END")
        assert not con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='capture_receipt'").fetchone()
    # A new capture records its own link without jumping past the unprocessed prefix.
    store.capture("poly_us", url, RECEIVED + 2001, RECEIVED + 2002, payload)
    with pytest.raises(ValueError, match="PROBABILITY_HISTORY_WARMING"):
        store.restore_probabilities([{"time": RECEIVED + 2002}], contract)
    with pytest.raises(sqlite3.IntegrityError, match="interrupted"):
        prepare_probability_history(store.path)
    with connect(store.path) as con:
        progress = con.execute("SELECT capture_id FROM probability_chart_progress").fetchone()[0]
        assert progress == 2000
        con.execute("DROP TRIGGER stop_probability_backfill")
    prepare_probability_history(store.path)
    restored = store.restore_probabilities([{"time": RECEIVED + 2001},
                                           {"time": RECEIVED + 2002}], contract)
    assert [p["probability"] for p in restored] == [0.31, 0.31]
    assert [p["probability_capture_id"] for p in restored] == [2001, 2002]
    assert [p["probability_received_at"] for p in restored] == [RECEIVED + 2001, RECEIVED + 2002]
    with connect(store.path, readonly=True) as con:
        progress = con.execute("SELECT capture_id FROM probability_chart_progress").fetchone()[0]
        assert progress == 2002
        plan = con.execute("EXPLAIN QUERY PLAN SELECT c.id FROM probability_capture_lookup l "
                           "JOIN captures c ON c.id=l.capture_id WHERE l.received=? AND c.url=?",
                           (RECEIVED, url)).fetchall()
        assert [row[1] for row in con.execute("PRAGMA table_info(probability_capture_lookup)")] == [
            "capture_id", "received"]
        assert any("probability_capture_receipt" in row[3] for row in plan)
        assert any("INTEGER PRIMARY KEY" in row[3] for row in plan)


def test_probability_prepare_bounds_online_captures_and_catches_up_next_run(tmp_path, monkeypatch):
    from contextlib import contextmanager

    store = FeedStore(tmp_path / "feed.sqlite3")
    url = "https://gateway.polymarket.us/v1/markets/tc-temp-nychigh-2026-09-27-lt65f/book"
    store.capture("poly_us", url, RECEIVED - 1, RECEIVED, b"{}")
    with connect(store.path) as con:
        body_hash = con.execute("SELECT hash FROM captures WHERE id=1").fetchone()[0]
        con.execute("INSERT INTO captures VALUES (2,'poly_us',?,?,?,?)",
                    (url, RECEIVED, RECEIVED + 1, body_hash))
    original_connect = connect
    appended = []

    @contextmanager
    def online_connect(path, *, readonly=False):
        with original_connect(path, readonly=readonly) as con:
            yield con
        # Simulate the old collector appending without maintaining the new projection.
        if readonly:
            with original_connect(path) as con:
                cursor = con.execute("INSERT INTO captures VALUES (NULL,'poly_us',?,?,?,?)",
                                     (url, RECEIVED, RECEIVED + 2 + len(appended), body_hash))
                appended.append(cursor.lastrowid)

    monkeypatch.setattr("nice_weather.trading.feed.connect", online_connect)
    prepare_probability_history(store.path)
    assert 1 <= len(appended) <= 3
    with original_connect(store.path, readonly=True) as con:
        progress = con.execute("SELECT capture_id FROM probability_chart_progress").fetchone()[0]
        assert progress == 2
        assert con.execute("SELECT MAX(id) FROM captures").fetchone()[0] > progress
    monkeypatch.setattr("nice_weather.trading.feed.connect", original_connect)
    prepare_probability_history(store.path)
    with original_connect(store.path, readonly=True) as con:
        progress = con.execute("SELECT capture_id FROM probability_chart_progress").fetchone()[0]
        assert progress == con.execute("SELECT MAX(id) FROM captures").fetchone()[0]

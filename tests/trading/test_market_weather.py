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
    feed.publish("book", "kalshi:2026-09-23:bin", {
        "bids": [[0.42, 2], [0.40, 100]], "asks": [[0.51, 3], [0.54, 100]],
        "received_at": None, "exchange_time": 1.5}, 3)
    quotes = scoped_history(feed, "kalshi", "2026-09-23", "kalshi:2026-09-23:bin")["points"]
    assert quotes[-1]["bids"] == [[0.42, 2]]
    assert quotes[-1]["asks"] == [[0.51, 3]]
    assert quotes[-1]["received_at"] is None
    assert quotes[-1]["exchange_time"] == 1.5
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


def test_weather_projection_backfills_repeated_snapshots_and_late_revisions(tmp_path):
    import json

    from nice_weather.trading.market_weather import prepare_weather_history

    feed = FeedStore(tmp_path / "feed.sqlite3")
    day = "2026-09-19"
    c = contract("kalshi", day)
    feed.publish("contracts", "kalshi", [c])
    start = datetime(2026, 9, 19, 5, tzinfo=UTC).timestamp()

    def observation(value, version):
        return {"station": "KNYC", "data": [{"icaoId": "KNYC", "obsTime": start,
                "temp": value, "revision_id": version, "first_received_at": start + 60}]}

    # Simulate an old database with more than two migration pages, plus late knowledge.
    with connect(feed.path) as con:
        con.executemany("INSERT INTO feed_events VALUES (NULL,'weather','metar',?,?)", [
            (start + 60 + i, json.dumps(observation(20, "a"))) for i in range(140)])
    feed.publish("weather", "metar", observation(21, "b"), start + 2 * 86400)
    feed.publish("weather", "metar", observation(20, "a"), start + 3 * 86400)
    # Publishing while the legacy cursor is behind must not skip A -> B -> A.
    prepare_weather_history(feed.path)
    points = weather_history(feed.path, "kalshi", day)["sources"]["metar"]
    assert [p["value"] for p in points] == pytest.approx([68, 69.8, 68])
    assert [p["received_at"] for p in points] == [start + 60, start + 2 * 86400,
                                                start + 3 * 86400]
    with connect(feed.path) as con:
        assert con.execute("SELECT COUNT(*) FROM weather_chart_points").fetchone()[0] == 3
        # A warm query reads compact facts, never the original repetitive bodies.
        con.execute("UPDATE feed_events SET body='not JSON' WHERE kind='weather'")
    assert weather_history(feed.path, "kalshi", day)["sources"]["metar"] == points
    # A new late correction is maintained in the same transaction as its source event.
    feed.publish("weather", "metar", observation(22, "c"), start + 4 * 86400)
    result = weather_history(feed.path, "kalshi", day)
    assert result["sources"]["metar"][-1]["received_at"] == start + 4 * 86400
    assert len(result["sources"]["metar"]) == 4
    assert weather_history(feed.path, "kalshi", "2026-09-20")["sources"]["metar"] == []


def test_weather_projection_keeps_pre_day_forecast_and_first_cli_receipt(tmp_path):
    feed = FeedStore(tmp_path / "feed.sqlite3")
    day = "2026-09-19"
    feed.publish("contracts", "kalshi", [contract("kalshi", day)])
    start = datetime(2026, 9, 19, 5, tzinfo=UTC).timestamp()
    forecast = {"station": "KNYC", "cycle": start - 3600,
                "points": [{"valid_at": start + 3600, "temperature_f": 72}]}
    feed.publish("weather", "hrrr", forecast, start - 1800)
    feed.publish("weather", "hrrr", forecast, start - 1200)
    report = {"station": "KNYC", "issued_at": "2026-09-20T10:00:00Z",
              "text": "THE CENTRAL PARK NY CLIMATE SUMMARY FOR SEPTEMBER 19 2026"}
    feed.publish("weather", "cli", report, start + 2 * 86400)
    feed.publish("weather", "cli", report, start + 3 * 86400)
    result = weather_history(feed.path, "kalshi", day)
    assert len(result["sources"]["hrrr"]) == 1
    assert result["sources"]["hrrr"][0]["received_at"] == start - 1800
    assert len(result["cli"]) == 1
    assert result["cli"][0]["received_at"] == start + 2 * 86400



def test_weather_backfill_uses_covering_index_and_global_order(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from nice_weather.trading import feed as feed_module
    from nice_weather.trading import market_weather

    statements, visited, expected = [], [], []

    @contextmanager
    def traced(*args, **kwargs):
        with connect(*args, **kwargs) as con:
            con.set_trace_callback(statements.append)
            yield con

    monkeypatch.setattr(feed_module, "connect", traced)
    monkeypatch.setattr(market_weather, "connect", traced)
    feed = FeedStore(tmp_path / "feed.sqlite3")
    with connect(feed.path) as con:
        assert not con.execute(
            "SELECT 1 FROM sqlite_master WHERE name='weather_event_seq'").fetchone()
        for i in range(170):
            # Unrelated large/invalid book bodies must never be scanned or decoded.
            con.execute("INSERT INTO feed_events VALUES (NULL,'book','bin',?,?)",
                        (i, 'x' * 65536))
            row = con.execute("INSERT INTO feed_events VALUES (NULL,'weather',?,?,'{}')",
                              (("metar", "hrrr", "cli")[i % 3], i))
            expected.append(row.lastrowid)
    project = market_weather.project_weather

    def record(con, seq, source, body, received):
        visited.append(seq)
        project(con, seq, source, body, received)

    monkeypatch.setattr(market_weather, "project_weather", record)
    market_weather.prepare_weather_history(feed.path)
    assert visited == expected  # Three 64-row batches keep global, not source-grouped order.
    latest = feed.publish("weather", "metar", {}, 999)
    assert visited == [*expected, latest]
    with connect(feed.path, readonly=True) as con:
        progress = con.execute("SELECT seq FROM weather_chart_progress WHERE id=1").fetchone()[0]
        assert progress == latest
        for sql in statements:
            if sql.startswith("SELECT ") and "FROM feed_events" in sql:
                plan = " ".join(row[3] for row in con.execute("EXPLAIN QUERY PLAN " + sql))
                if "body" in sql:
                    assert "USING INTEGER PRIMARY KEY" in plan
                else:
                    assert "COVERING INDEX feed_history" in plan


def test_probability_history_warming_is_503_and_recovers_after_prepare(tmp_path):
    import json

    from nice_weather.trading.feed import prepare_probability_history

    app = create_app(tmp_path, password="test", origin="http://testserver")
    feed = FeedStore(tmp_path / "feed.sqlite3")
    c = contract("poly_us", "2026-09-27") | {"condition_id": "tc-temp-nychigh-2026-09-27-lt65f"}
    received = datetime(2026, 9, 27, 5, tzinfo=UTC).timestamp()
    feed.publish("contracts", "poly_us", [c], received)
    feed.publish("book", c["yes_token_id"], {"bids": [], "asks": []}, received)
    native = {"marketData": {"marketSlug": c["condition_id"], "stats": {
        "lastPriceSample": {"longPx": {"value": "0.2"}, "ts": "2026-09-27T04:59:00Z"}}}}
    feed.capture("poly_us", f'https://gateway.polymarket.us/v1/markets/{c["condition_id"]}/book',
                 received - 1, received, json.dumps(native).encode())
    with connect(feed.path) as con:
        con.execute("UPDATE probability_chart_progress SET capture_id=0")
    client = TestClient(app)
    client.post("/api/login", json={"password": "test"}, headers={"origin": "http://testserver"})
    params = {"token": c["yes_token_id"], "venue": "poly_us", "day": "2026-09-27"}
    warming = client.get("/api/history", params=params)
    assert warming.status_code == 503
    assert warming.json() == {"detail": "PROBABILITY_HISTORY_WARMING"}
    invalid = client.get("/api/history", params=params | {"token": "unknown"})
    assert invalid.status_code == 400
    prepare_probability_history(feed.path)
    ready = client.get("/api/history", params=params)
    assert ready.status_code == 200
    assert ready.json()["points"][0]["probability"] == 0.2


def test_weather_backfill_retries_batch_when_another_writer_advances_cursor(tmp_path, monkeypatch):
    import json
    from contextlib import contextmanager

    from nice_weather.trading import market_weather

    feed = FeedStore(tmp_path / "feed.sqlite3")
    with connect(feed.path) as con:
        con.executemany("INSERT INTO feed_events VALUES (NULL,'weather','metar',?,'{}')",
                        [(i,) for i in range(70)])
        expected = [row[0] for row in con.execute("SELECT seq FROM feed_events ORDER BY seq")]
    visited = []
    project = market_weather.project_weather

    def record(con, seq, source, body, received):
        visited.append(seq)
        project(con, seq, source, body, received)

    injected = False

    @contextmanager
    def concurrent(*args, **kwargs):
        nonlocal injected
        with connect(*args, **kwargs) as con:
            statements = []
            con.set_trace_callback(statements.append)
            yield con
            if (kwargs.get("readonly") and not injected and any(
                    sql.startswith("SELECT seq,key,received,body") for sql in statements)):
                injected = True
                # The read snapshot stays open: unrelated writes must still be possible.
                with connect(feed.path) as writer:
                    writer.execute("BEGIN IMMEDIATE")
                    rows = writer.execute("SELECT seq,key,received,body FROM feed_events "
                                          "ORDER BY seq LIMIT 64").fetchall()
                    for row in rows:
                        record(writer, row["seq"], row["key"], json.loads(row["body"]),
                               row["received"])
                    writer.execute("UPDATE weather_chart_progress SET seq=?", (rows[-1]["seq"],))

    monkeypatch.setattr(market_weather, "connect", concurrent)
    monkeypatch.setattr(market_weather, "project_weather", record)
    market_weather.prepare_weather_history(feed.path)
    assert injected
    assert visited == expected  # Discard the stale first batch; never project it twice.
    with connect(feed.path, readonly=True) as con:
        assert con.execute("SELECT seq FROM weather_chart_progress").fetchone()[0] == expected[-1]

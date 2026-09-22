"""Append-only public KNYC capture and bounded terminal projections."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import re
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from nice_weather.trading.storage import connect, encoded
from nice_weather.trading.us_markets import (
    CLIMATE_ZONE,
    KALSHI,
    VENUES,
    book_url,
    event_url,
    normalize,
    normalize_book,
)


class FeedStore:
    def __init__(self, path: Path):
        self.path = path
        with connect(path) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS capture_bodies (
                    hash TEXT PRIMARY KEY, body BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS feed_events (
                    seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
                    received REAL NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS feed_history ON feed_events(kind,key,seq);
                CREATE TABLE IF NOT EXISTS feed_latest (
                    kind TEXT NOT NULL, key TEXT NOT NULL, seq INTEGER NOT NULL,
                    received REAL NOT NULL, body TEXT NOT NULL, PRIMARY KEY(kind,key));
                CREATE TABLE IF NOT EXISTS captures (
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL, url TEXT NOT NULL,
                    requested REAL NOT NULL, received REAL NOT NULL, hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observation_receipts (
                    station TEXT NOT NULL, observed REAL NOT NULL, received REAL NOT NULL,
                    PRIMARY KEY(station,observed));
                CREATE TABLE IF NOT EXISTS observation_revisions (
                    station TEXT NOT NULL, observed REAL NOT NULL, revision TEXT NOT NULL,
                    received REAL NOT NULL, PRIMARY KEY(station,observed,revision));
                CREATE TABLE IF NOT EXISTS settlement_watch (
                    venue TEXT NOT NULL, day TEXT NOT NULL, contracts TEXT NOT NULL,
                    checked REAL NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(venue,day));
            """)

    def require_space(self):
        # Preserve headroom for order journals and the existing KLGA services.
        # Stop new bulk captures; never delete observations or financial records.
        if shutil.disk_usage(self.path.parent).free < 1024**3:
            raise OSError("KNYC capture paused: less than 1 GiB disk headroom")

    def capture(self, source, url, requested, received, body):
        self.require_space()
        key = hashlib.sha256(body).hexdigest()
        with connect(self.path) as con:
            con.execute(
                "INSERT OR IGNORE INTO capture_bodies VALUES (?,?)", (key, gzip.compress(body))
            )
            cursor = con.execute(
                "INSERT INTO captures VALUES (NULL,?,?,?,?,?)",
                (source, url, requested, received, key),
            )
            return cursor.lastrowid

    def publish(self, kind, key, body, received=None):
        if kind != "health":
            self.require_space()
        received = time.time() if received is None else received
        text = encoded(body)
        with connect(self.path) as con:
            cursor = con.execute(
                "INSERT INTO feed_events VALUES (NULL,?,?,?,?)", (kind, key, received, text)
            )
            con.execute(
                "INSERT OR REPLACE INTO feed_latest VALUES (?,?,?,?,?)",
                (kind, key, cursor.lastrowid, received, text),
            )
            if kind == "contracts" and body and body[0].get("local_day"):
                con.execute(
                    "INSERT INTO settlement_watch(venue,day,contracts) VALUES (?,?,?) "
                    "ON CONFLICT(venue,day) DO UPDATE SET contracts=excluded.contracts",
                    (key, body[0]["local_day"], text),
                )
            return cursor.lastrowid

    def snapshot(self):
        with connect(self.path, readonly=True) as con:
            con.execute("BEGIN")  # Latest rows and cursor must belong to the same WAL snapshot.
            rows = con.execute(
                "SELECT * FROM feed_latest WHERE kind IN ('contracts','weather','health')"
            ).fetchall()
            tokens = [contract["yes_token_id"] for row in rows if row["kind"] == "contracts"
                      for contract in json.loads(row["body"])]
            if tokens:
                placeholders = ",".join("?" for _ in tokens)
                rows += con.execute(
                    "SELECT * FROM feed_latest WHERE kind='book' AND key IN ("
                    + placeholders + ")", tokens).fetchall()
            cursor = con.execute("SELECT COALESCE(MAX(seq),0) FROM feed_events").fetchone()[0]
        output = {"cursor": cursor, "contracts": [], "books": {}, "weather": {}, "health": {}}
        for row in rows:
            body = json.loads(row["body"])
            if row["kind"] == "contracts":
                output["contracts"].extend(body)
            elif row["kind"] in {"book", "weather", "health"}:
                group = "books" if row["kind"] == "book" else row["kind"]
                output[group][row["key"]] = body
        return output

    def observation_receipts(self, rows, received):
        """First knowledge is durable; repeated polling cannot renew a cross-bin event."""
        with connect(self.path) as con:
            result = []
            for row in rows:
                stamp = row.get("obsTime")
                if row.get("icaoId") != "KNYC" or type(stamp) not in (int, float):
                    continue
                con.execute("INSERT OR IGNORE INTO observation_receipts VALUES ('KNYC',?,?)",
                            (stamp, received))
                first = con.execute("SELECT received FROM observation_receipts "
                                    "WHERE station='KNYC' AND observed=?", (stamp,)).fetchone()[0]
                revision = hashlib.sha256(encoded({"temp": row.get("temp"),
                                                   "rawOb": row.get("rawOb")}).encode()).hexdigest()
                con.execute("INSERT OR IGNORE INTO observation_revisions VALUES ('KNYC',?,?,?)",
                            (stamp, revision, received))
                revision_received = con.execute(
                    "SELECT received FROM observation_revisions WHERE station='KNYC' "
                    "AND observed=? AND revision=?", (stamp, revision)).fetchone()[0]
                result.append(row | {"first_received_at": revision_received,
                                     "observation_first_received_at": first,
                                     "revision_id": revision})
            return result

    def since(self, cursor, limit=256):
        with connect(self.path, readonly=True) as con:
            return [
                {
                    "seq": r["seq"],
                    "kind": r["kind"],
                    "key": r["key"],
                    "received": r["received"],
                    "data": json.loads(r["body"]),
                }
                for r in con.execute(
                    "SELECT * FROM feed_events WHERE seq>? ORDER BY seq LIMIT ?", (cursor, limit)
                )
            ]

    def history(self, token, before=None, limit=1500):
        with connect(self.path, readonly=True) as con:
            rows = con.execute(
                "SELECT seq,received,body FROM feed_events WHERE kind='book' AND key=? "
                "AND seq<? ORDER BY seq DESC LIMIT ?",
                (token, before or 2**63 - 1, limit),
            ).fetchall()
        return [
            {"seq": r["seq"], "time": r["received"], **json.loads(r["body"])}
            for r in reversed(rows)
        ]


async def capture_json(client, store, source, url):
    started = time.time()
    response = await client.get(url)
    received = time.time()
    capture_id = store.capture(source, url, started, received, response.content)
    response.raise_for_status()
    return response.json(), received, capture_id


async def settlement_feed(store, stop):
    from nice_weather.trading.us_markets import POLY_US, final_value

    async with httpx.AsyncClient(timeout=10) as client:
        while not stop.is_set():
            now = time.time()
            with connect(store.path) as con:
                pending = con.execute(
                    "SELECT * FROM settlement_watch WHERE done=0 AND checked<? "
                    "ORDER BY checked LIMIT 2", (now - 300,)).fetchall()
            for watch in pending:
                venue, day = watch["venue"], watch["day"]
                with connect(store.path) as con:
                    con.execute("UPDATE settlement_watch SET checked=? WHERE venue=? AND day=?",
                                (now, venue, day))
                try:
                    payload, received, capture_id = await capture_json(
                        client, store, venue + "-settlement", event_url(venue, day))
                    rows = payload["markets"] if venue == "kalshi" else payload["event"]["markets"]
                    field = "ticker" if venue == "kalshi" else "slug"
                    markets = {raw[field]: raw for raw in rows}
                    complete = True
                    for contract in json.loads(watch["contracts"]):
                        with connect(store.path, readonly=True) as con:
                            prior = con.execute(
                                "SELECT 1 FROM feed_latest WHERE kind='settlement' AND key=?",
                                (contract["yes_token_id"],)).fetchone()
                        if prior:
                            continue
                        raw = markets[contract["condition_id"]]
                        final_status = ("finalized" if venue == "kalshi"
                                        else "MARKET_STATUS_RESOLVED")
                        if raw.get("status") != final_status:
                            complete = False
                            continue
                        evidence, captures = {"market": raw}, [capture_id]
                        if venue == "poly_us":
                            # Settlement confirmation is separate from displayed outcome prices.
                            confirmation, received, confirm_id = await capture_json(
                                client, store, "poly_us-settlement",
                                f'{POLY_US}/markets/{contract["condition_id"]}/settlement')
                            evidence["confirmation"] = confirmation
                            captures.append(confirm_id)
                        payout = final_value(contract, evidence, received)
                        store.publish("settlement", contract["yes_token_id"],
                                      {"venue": venue, "source_payload": evidence,
                                       "value": float(payout), "capture_ids": captures}, received)
                        if venue == "poly_us":
                            # Space confirmations; a 429 aborts this poll and retries after 5 min.
                            await asyncio.sleep(1)
                    if complete:
                        with connect(store.path) as con:
                            con.execute(
                                "UPDATE settlement_watch SET done=1 WHERE venue=? AND day=?",
                                (venue, day))
                    store.publish("health", venue + "-settlement",
                                  {"status": "connected", "received_at": time.time(),
                                   "day": day, "final": complete})
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    store.publish("health", venue + "-settlement",
                                  {"status": "disconnected", "received_at": time.time(),
                                   "day": day, "reason": type(exc).__name__})
            try:
                await asyncio.wait_for(stop.wait(), 30)
            except TimeoutError:
                pass


async def market_feed(store, venue, stop, interval=2):
    contracts, day, next_metadata = [], None, 0
    directory_task = None
    async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "nice-weather/0.1"}) as client:
        while not stop.is_set():
            started = time.monotonic()
            try:
                current = datetime.now(CLIMATE_ZONE).date().isoformat()
                if current != day or time.time() >= next_metadata:
                    series = None
                    if venue == "kalshi":
                        series, _, _ = await capture_json(
                            client, store, venue, f"{KALSHI}/series/KXHIGHNY"
                        )
                        series = series["series"]
                    payload, received, _ = await capture_json(
                        client, store, venue, event_url(venue, current)
                    )
                    contracts = normalize(venue, current, payload, received, series)
                    store.publish("contracts", venue, contracts, received)
                    day, next_metadata = current, time.time() + 300
                    # Directory facts are separate from the active contracts consumed by trading.
                    from nice_weather.trading.market_discovery import refresh_directory

                    if directory_task is None or directory_task.done():
                        directory_task = asyncio.create_task(
                            refresh_directory(client, store, venue, current, series))

                async def fetch_book(contract):
                    payload, received, _ = await capture_json(
                        client, store, venue, book_url(contract)
                    )
                    book = normalize_book(venue, payload, received)
                    store.publish("book", contract["yes_token_id"], book, received)

                results = await asyncio.gather(
                    *(fetch_book(c) for c in contracts), return_exceptions=True
                )
                failed = sum(isinstance(r, Exception) for r in results)
                store.publish(
                    "health",
                    venue,
                    {
                        "status": "degraded" if failed else "connected",
                        "transport": "REST",
                        "interval_seconds": interval,
                        "message": f"{failed} book failures" if failed else "Public snapshots",
                        "received_at": time.time(),
                    },
                )
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                store.publish(
                    "health",
                    venue,
                    {
                        "status": "disconnected",
                        "transport": "REST",
                        "message": "Public feed unavailable or schema changed",
                        "received_at": time.time(),
                    },
                )
            try:
                await asyncio.wait_for(
                    stop.wait(), max(0.05, interval - (time.monotonic() - started))
                )
            except TimeoutError:
                pass

        if directory_task is not None:
            directory_task.cancel()
            await asyncio.gather(directory_task, return_exceptions=True)


async def weather_feed(store, stop):
    """Persist each source version with real receipt. CLI is evidence, never a live label."""
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "nice-weather/0.1"}) as client:
        while not stop.is_set():
            now = datetime.now(UTC)
            urls = {
                "metar": "https://aviationweather.gov/api/data/metar?ids=KNYC&format=json&hours=24",
                "nws_observations": "https://api.weather.gov/stations/KNYC/observations?start="
                + (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "cli_index": "https://api.weather.gov/products/types/CLI/locations/NYC",
            }
            for source, url in urls.items():
                try:
                    payload, received, capture_id = await capture_json(client, store, source, url)
                    if source == "cli_index":
                        for product in reversed(payload.get("@graph", [])[:2]):
                            product_id = product.get("id", "")
                            if not isinstance(product_id, str) or not re.fullmatch(
                                r"[a-fA-F0-9-]{36}", product_id
                            ):
                                continue
                            text, stamp, capture = await capture_json(
                                client,
                                store,
                                "cli",
                                "https://api.weather.gov/products/" + product_id,
                            )
                            store.publish(
                                "weather",
                                "cli",
                                {
                                    "station": "KNYC",
                                    "received_at": stamp,
                                    "capture_id": capture,
                                    "issued_at": text.get("issuanceTime"),
                                    "text": text.get("productText"),
                                    "finality": "unverified",
                                },
                                stamp,
                            )
                    else:
                        if source == "metar":
                            payload = store.observation_receipts(payload, received)
                        store.publish(
                            "weather",
                            source,
                            {
                                "station": "KNYC",
                                "received_at": received,
                                "capture_id": capture_id,
                                "data": payload,
                            },
                            received,
                        )
                    store.publish(
                        "health", source, {"status": "connected", "received_at": time.time()}
                    )
                except (httpx.HTTPError, KeyError, ValueError, TypeError):
                    store.publish(
                        "health",
                        source,
                        {
                            "status": "disconnected",
                            "received_at": time.time(),
                            "message": "Weather source unavailable",
                        },
                    )
            from nice_weather.trading.knyc_model import publish_predictions

            publish_predictions(store)
            try:
                await asyncio.wait_for(stop.wait(), 30)
            except TimeoutError:
                pass


async def run(root, once=False):
    store, stop = FeedStore(root / "feed.sqlite3"), asyncio.Event()
    # Keep WAL sidecars alive between short durable writes, as in the native workers.
    # This connection holds no transaction and performs no feed writes itself.
    with connect(store.path):
        tasks = [asyncio.create_task(market_feed(store, v, stop)) for v in VENUES]
        tasks.append(asyncio.create_task(weather_feed(store, stop)))
        tasks.append(asyncio.create_task(settlement_feed(store, stop)))
        try:
            if once:
                await asyncio.sleep(20)
                stop.set()
            await asyncio.gather(*tasks)
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.root, args.once))

"""Verified R2 eviction of KNYC weather capture bodies; metadata stays local."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from nice_weather.r2_archive import R2Config
from nice_weather.trading.storage import WEATHER_SOURCES, connect


def _checkpoint_wal(path: Path):
    """Try to checkpoint and release the WAL without waiting on active readers."""
    with sqlite3.connect(path, timeout=0) as con:
        busy, log_frames, checkpointed_frames = con.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    wal = Path(str(path) + "-wal")
    try:
        wal_bytes = wal.stat().st_size
    except FileNotFoundError:
        wal_bytes = 0
    return {"busy": bool(busy), "log_frames": log_frames,
            "checkpointed_frames": checkpointed_frames, "wal_bytes": wal_bytes}


def sync(path: Path, client, config: R2Config, *, prune=False):
    """Upload complete capture rows and bytes, GET-verify, then optionally evict bytes."""
    placeholders = ",".join("?" for _ in WEATHER_SOURCES)
    with connect(path) as con:
        con.execute("CREATE INDEX IF NOT EXISTS captures_hash ON captures(hash)")
        con.execute("""CREATE TABLE IF NOT EXISTS weather_raw_archives (
            hash TEXT PRIMARY KEY, object_key TEXT NOT NULL, sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, verified_at REAL NOT NULL)""")
    _checkpoint_wal(path)
    count = 0
    with connect(path, readonly=True) as con:
        upper = con.execute("SELECT COALESCE(MAX(hash),'') FROM capture_bodies").fetchone()[0]
    after = ""
    while after < upper:
        # Scan stored bodies directly; the metadata hash index bounds source checks.
        with connect(path, readonly=True) as con:
            page = con.execute(
                "SELECT b.hash,b.body FROM capture_bodies b WHERE b.hash>? AND b.hash<=? "
                "AND length(b.body)>0 "
                "AND EXISTS(SELECT 1 FROM captures c WHERE c.hash=b.hash "
                f"AND c.source IN ({placeholders})) "
                "AND NOT EXISTS(SELECT 1 FROM captures c WHERE c.hash=b.hash "
                f"AND c.source NOT IN ({placeholders})) ORDER BY b.hash LIMIT 50",
                (after, upper, *WEATHER_SOURCES, *WEATHER_SOURCES),
            ).fetchall()
        if not page:
            break
        after = page[-1]["hash"]
        records = []
        for row in page:
            raw = bytes(row["body"])
            if hashlib.sha256(gzip.decompress(raw)).hexdigest() != row["hash"]:
                raise RuntimeError("KNYC capture hash mismatch; bytes retained")
            with connect(path, readonly=True) as con:
                captures = [dict(c) for c in con.execute(
                    f"SELECT * FROM captures WHERE hash=? AND source IN ({placeholders}) "
                    "ORDER BY id", (row["hash"], *WEATHER_SOURCES))]
            records.append({"hash": row["hash"],
                            "body_base64": base64.b64encode(raw).decode(),
                            "captures": captures})
        payload = gzip.compress(json.dumps(
            {"version": 1, "station": "KNYC", "records": records}, sort_keys=True).encode(),
            mtime=0)
        digest = hashlib.sha256(payload).hexdigest()
        key = f"knyc/v1/weather-raw/{digest}.json.gz"
        client.put_object(Bucket=config.bucket, Key=key, Body=payload,
                          ContentType="application/gzip", Metadata={"sha256": digest})
        body = client.get_object(Bucket=config.bucket, Key=key)["Body"]
        try:
            downloaded = body.read()
        finally:
            if hasattr(body, "close"):
                body.close()
        if downloaded != payload:
            raise RuntimeError("KNYC R2 readback mismatch; bytes retained")
        with connect(path) as con:
            for row in page:
                con.execute("INSERT OR REPLACE INTO weather_raw_archives VALUES (?,?,?,?,?)",
                            (row["hash"], key, digest, len(payload), time.time()))
                if prune:
                    # A market can start sharing these bytes during upload/verification.
                    count += con.execute(
                        "UPDATE capture_bodies SET body=X'' WHERE hash=? AND body=? "
                        "AND NOT EXISTS(SELECT 1 FROM captures c "
                        "WHERE c.hash=capture_bodies.hash "
                        f"AND c.source NOT IN ({placeholders}))",
                        (row["hash"], row["body"], *WEATHER_SOURCES),
                    ).rowcount
        checkpoint = _checkpoint_wal(path) if prune else None
        print(json.dumps({"object_key": key, "verified": True, "pruned": count,
                          "wal_checkpoint": checkpoint}), flush=True)
    if prune:
        with connect(path) as con:
            con.execute("PRAGMA incremental_vacuum(4096)").fetchall()
        print(json.dumps({"wal_checkpoint_final": _checkpoint_wal(path)}), flush=True)
    return count


def prune_market_history(path: Path, results_path: Path, *, now=None, max_pages=64):
    """Bound old quotes and transient HRRR after both Paper checkpoints."""
    if not results_path.is_file():
        return 0
    accounts = ("sandbox-kalshi-knyc", "sandbox-poly_us-knyc")
    with connect(results_path, readonly=True) as con:
        rows = con.execute(
            "SELECT account,config FROM runs WHERE account IN (?,?) AND mode='sandbox'",
            accounts,
        ).fetchall()
    if {row["account"] for row in rows} != set(accounts):
        return 0
    try:
        cursors = [json.loads(row["config"])["feed_cursor"] for row in rows]
    except (TypeError, ValueError, KeyError):
        return 0
    if any(type(cursor) is not int or cursor < 1 for cursor in cursors):
        return 0
    cutoff = (time.time() if now is None else now) - 86400
    with connect(path, readonly=True) as con:
        # Keep the high-water row so SQLite cannot reuse its sequence number.
        ceiling = min(min(cursors), con.execute(
            "SELECT COALESCE(MAX(seq),0)-1 FROM feed_events").fetchone()[0])
    if ceiling < 1:
        return 0
    with connect(path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS market_history_progress ("
                    "id INTEGER PRIMARY KEY CHECK(id=1), seq INTEGER NOT NULL, "
                    "evicted_before REAL NOT NULL)")
        con.execute("INSERT OR IGNORE INTO market_history_progress VALUES (1,0,0)")
    deleted = 0
    for _ in range(max_pages):
        with connect(path) as con:
            progress = con.execute(
                "SELECT seq FROM market_history_progress WHERE id=1").fetchone()[0]
            page = con.execute(
                "SELECT seq,received FROM feed_events WHERE seq>? AND seq<=? "
                "ORDER BY seq LIMIT 1000", (progress, ceiling),
            ).fetchall()
            prefix = []
            for row in page:
                if row["received"] >= cutoff:
                    break
                prefix.append(row)
            if not prefix:
                break
            end = prefix[-1]["seq"]
            con.execute(
                "DELETE FROM weather_chart_points WHERE source='hrrr' "
                "AND seq>? AND seq<=?",
                (progress, end),
            )
            changed = con.execute(
                "DELETE FROM feed_events WHERE seq>? AND seq<=? "
                "AND (kind IN ('book','market_price') OR "
                "(kind='weather' AND key='hrrr')) AND received<?",
                (progress, end, cutoff),
            ).rowcount
            deleted += changed
            con.execute(
                "UPDATE market_history_progress SET seq=?, "
                "evicted_before=CASE WHEN ?>0 THEN MAX(evicted_before,?) "
                "ELSE evicted_before END WHERE id=1", (end, changed, cutoff),
            )
        checkpoint = _checkpoint_wal(path)
        if checkpoint["busy"] and checkpoint["wal_bytes"] > 128 * 1024**2:
            break
        if len(prefix) < len(page):
            break
    if deleted:
        with connect(path) as con:
            con.execute("PRAGMA incremental_vacuum(4096)").fetchall()
        print(json.dumps({"transient_events_pruned": deleted,
                          "wal_checkpoint": _checkpoint_wal(path)}), flush=True)
    return deleted


def main():
    import boto3

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--prune", action="store_true")
    args = parser.parse_args()
    if not args.db.is_file():
        parser.error("Database must already exist")
    config = R2Config.from_env()
    client = boto3.client("s3", endpoint_url=config.endpoint_url,
                          aws_access_key_id=config.access_key_id,
                          aws_secret_access_key=config.secret_access_key, region_name="auto")
    sync(args.db, client, config, prune=args.prune)
    if args.prune:
        prune_market_history(args.db, args.db.with_name("results.sqlite3"))


if __name__ == "__main__":
    main()

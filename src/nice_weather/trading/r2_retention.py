"""Verified R2 eviction of KNYC weather capture bodies; metadata stays local."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import time
from pathlib import Path

from nice_weather.r2_archive import R2Config
from nice_weather.trading.storage import WEATHER_SOURCES, connect


def sync(path: Path, client, config: R2Config, *, prune=False):
    """Upload complete capture rows and bytes, GET-verify, then optionally evict bytes."""
    placeholders = ",".join("?" for _ in WEATHER_SOURCES)
    with connect(path) as con:
        con.execute("CREATE INDEX IF NOT EXISTS captures_hash ON captures(hash)")
        con.execute("""CREATE TABLE IF NOT EXISTS weather_raw_archives (
            hash TEXT PRIMARY KEY, object_key TEXT NOT NULL, sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL, verified_at REAL NOT NULL)""")
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
        print(json.dumps({"object_key": key, "verified": True, "pruned": count}), flush=True)
    if prune:
        with connect(path) as con:
            con.execute("PRAGMA incremental_vacuum(4096)").fetchall()
    return count


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


if __name__ == "__main__":
    main()

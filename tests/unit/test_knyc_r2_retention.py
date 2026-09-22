import gzip
import hashlib
import io
import runpy
import sqlite3
from pathlib import Path

import pytest

from nice_weather.r2_archive import R2Config
from nice_weather.trading.r2_retention import sync


class S3:
    def __init__(self):
        self.objects = {}
        self.corrupt = True

    def put_object(self, **kw):
        self.objects[kw["Key"]] = kw["Body"]

    def get_object(self, **kw):
        return {"Body": io.BytesIO(b"corrupt" if self.corrupt else self.objects[kw["Key"]])}


def test_verified_eviction_preserves_metadata_and_market_bytes(tmp_path):
    path = tmp_path / "feed.sqlite3"
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE capture_bodies(hash TEXT PRIMARY KEY,body BLOB NOT NULL);
            CREATE TABLE captures(id INTEGER PRIMARY KEY,source TEXT,url TEXT,
                requested REAL,received REAL,hash TEXT);
        """)
        for i, source in enumerate(("metar", "kalshi", "nws_observations")):
            body = source.encode()
            digest = hashlib.sha256(body).hexdigest()
            con.execute("INSERT INTO capture_bodies VALUES (?,?)", (digest, gzip.compress(body)))
            con.execute("INSERT INTO captures VALUES (?,?,?,?,?,?)",
                        (i, source, "https://example.test", 1, 2, digest))
    config = R2Config("https://example.test", "weather", "test", "test")
    client = S3()
    with pytest.raises(RuntimeError, match="readback"):
        sync(path, client, config, prune=True)
    with sqlite3.connect(path) as con:
        count = con.execute(
            "SELECT count(*) FROM capture_bodies WHERE length(body)>0").fetchone()[0]
        assert count == 3
    client.corrupt = False
    assert sync(path, client, config, prune=True) == 2
    assert sync(path, client, config, prune=True) == 0
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT count(*) FROM captures WHERE received=2").fetchone()[0] == 3
        count = con.execute(
            "SELECT count(*) FROM capture_bodies WHERE length(body)>0").fetchone()[0]
        assert count == 1
        assert con.execute("SELECT count(*) FROM weather_raw_archives").fetchone()[0] == 2


def test_legacy_weather_export_excludes_market_rows(tmp_path):
    archive = runpy.run_path(str(Path(__file__).parents[2] / "scripts/archive_legacy_weather.py"))
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE raw_snapshots(source TEXT,payload_json TEXT);
            INSERT INTO raw_snapshots VALUES ('nws','weather'),('polymarket_gamma','market');
            CREATE TABLE paper_orders(id TEXT);
            INSERT INTO paper_orders VALUES ('private');
        """)
    client = S3()
    client.corrupt = False
    result = archive["archive"](path, client, "weather")
    assert result["tables"]["raw_snapshots"]["rows"] == 1
    assert "paper_orders" not in result["tables"]
    for payload in client.objects.values():
        assert b"private" not in gzip.decompress(payload)
        assert b"polymarket_gamma" not in gzip.decompress(payload)

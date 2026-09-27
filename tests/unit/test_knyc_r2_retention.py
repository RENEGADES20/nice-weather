import gzip
import hashlib
import io
import json
import runpy
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from nice_weather.r2_archive import R2Config
from nice_weather.trading.r2_retention import sync


class S3:
    def __init__(self):
        self.objects = {}
        self.corrupt = True
        self.uploads = []

    def put_object(self, **kw):
        self.uploads.append(kw["Key"])
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
    target = tmp_path / "compact.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("INSERT INTO raw_snapshots VALUES ('new_weather_source','keep')")
        con.execute("CREATE INDEX raw_source ON raw_snapshots(source)")
    assert archive["compact_weather"](path, target) == {"raw_snapshots": 2}
    with sqlite3.connect(target) as con:
        assert con.execute("SELECT * FROM paper_orders").fetchall() == []
        assert set(con.execute("SELECT * FROM raw_snapshots")) == {
            ("nws", "weather"), ("new_weather_source", "keep")}
        assert con.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT * FROM paper_orders").fetchall() == [("private",)]



def captured_database(tmp_path, records):
    path = tmp_path / "feed.sqlite3"
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE capture_bodies(hash TEXT PRIMARY KEY,body BLOB NOT NULL);
            CREATE TABLE captures(id INTEGER PRIMARY KEY,source TEXT,url TEXT,
                requested REAL,received REAL,hash TEXT);
        """)
        for i, (source, raw) in enumerate(records, 1):
            digest = hashlib.sha256(raw).hexdigest()
            con.execute("INSERT OR IGNORE INTO capture_bodies VALUES (?,?)",
                        (digest, gzip.compress(raw)))
            con.execute("INSERT INTO captures VALUES (?,?,?,?,?,?)",
                        (i, source, "https://example.test", i, i + 1, digest))
    return path


@pytest.mark.parametrize("prune", [False, True])
def test_sparse_weather_uses_metadata_pages_and_body_point_lookups(tmp_path, monkeypatch, prune):
    from nice_weather.trading import r2_retention

    selected = {2: ("metar", b"repeated weather"), 2002: ("metar", b"repeated weather"),
                2050: ("cli", b"report"), 4070: ("metar", b"shared with market"),
                4080: ("kalshi", b"shared with market"),
                4090: ("nws_observations", b"observation")}
    records = [selected.get(i, ("kalshi", f"market {i}".encode())) for i in range(4100)]
    path = captured_database(tmp_path, records)
    client = S3()
    client.corrupt = False
    config = R2Config("https://example.test", "weather", "test", "test")
    connection = r2_retention.connect
    queries = []

    @contextmanager
    def traced(*args, **kwargs):
        with connection(*args, **kwargs) as con:
            con.set_trace_callback(queries.append)
            yield con

    monkeypatch.setattr(r2_retention, "connect", traced)
    assert sync(path, client, config, prune=prune) == (3 if prune else 0)
    archived = [record for value in client.objects.values()
                for record in json.loads(gzip.decompress(value))["records"]]
    expected = {hashlib.sha256(raw).hexdigest() for raw in
                (b"repeated weather", b"report", b"observation")}
    assert {record["hash"] for record in archived} == expected
    assert len(client.uploads) == 3  # Repeated weather spans pages, including without pruning.
    assert all(row["source"] != "kalshi" for record in archived for row in record["captures"])
    repeated = next(record for record in archived if len(record["captures"]) == 2)
    assert repeated["hash"] == hashlib.sha256(b"repeated weather").hexdigest()
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 4100
        pruned = con.execute(
            "SELECT COUNT(*) FROM capture_bodies WHERE length(body)=0").fetchone()[0]
        assert pruned == (3 if prune else 0)
        assert con.execute("SELECT length(body)>0 FROM capture_bodies WHERE hash=?",
                           (hashlib.sha256(b"shared with market").hexdigest(),)).fetchone()[0] == 1
        # Exercise the actual body queries: the large table must receive equality seeks only.
        body_reads = [q for q in queries if q.startswith("SELECT") and "FROM capture_bodies b" in q]
        assert body_reads
        for query in body_reads:
            plan = [row[-1] for row in con.execute("EXPLAIN QUERY PLAN " + query)]
            assert any("SEARCH b " in step and "(hash=?)" in step for step in plan)
    if prune:
        before = len(client.uploads)
        assert sync(path, client, config, prune=True) == 0
        assert len(client.uploads) == before


@pytest.mark.parametrize("when", ["metadata", "upload"])
def test_new_market_reference_never_exports_market_metadata_or_prunes(tmp_path, monkeypatch, when):
    from nice_weather.trading import r2_retention

    raw = b"weather now shared with a market"
    digest = hashlib.sha256(raw).hexdigest()
    path = captured_database(tmp_path, [("metar", raw)])

    def add_market():
        with sqlite3.connect(path) as con:
            con.execute("INSERT INTO captures VALUES (2,'kalshi',?,3,4,?)",
                        ("https://example.test/market", digest))

    connection = r2_retention.connect

    @contextmanager
    def concurrent_connection(*args, **kwargs):
        with connection(*args, **kwargs) as con:
            class Reader:
                def execute(self, query, params=()):
                    if when == "metadata" and query.startswith("SELECT * FROM captures"):
                        # The eligibility/body query completed, but its read snapshot ended.
                        add_market()
                    return con.execute(query, params)

            yield Reader() if kwargs.get("readonly") else con

    monkeypatch.setattr(r2_retention, "connect", concurrent_connection)

    class ConcurrentMarket(S3):
        def put_object(self, **kwargs):
            super().put_object(**kwargs)
            if when == "upload":
                add_market()

    client = ConcurrentMarket()
    client.corrupt = False
    config = R2Config("https://example.test", "weather", "test", "test")
    assert sync(path, client, config, prune=True) == 0
    records = [record for value in client.objects.values()
               for record in json.loads(gzip.decompress(value))["records"]]
    assert [row["source"] for record in records for row in record["captures"]] == ["metar"]
    with sqlite3.connect(path) as con:
        assert gzip.decompress(con.execute("SELECT body FROM capture_bodies WHERE hash=?",
                                           (digest,)).fetchone()[0]) == raw
        assert con.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM weather_raw_archives").fetchone()[0] == 1

"""Archive a stopped legacy SQLite database's weather tables, with R2 readback.

No deletion. The returned manifest records exact row counts, schemas and hashes.
Market/account tables are excluded. Used before retiring an old mixed database.
"""

import argparse
import base64
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from nice_weather.r2_archive import R2Config

TABLES = (
    "source_captures", "poll_attempts", "weather_observations", "weather_forecasts",
    "forecast_points", "settlement_evidence", "settlement_rows", "weather_feature_snapshots",
    "weather_daily_labels", "model_predictions", "raw_snapshots",
)
WEATHER = ("aviationweather", "nws", "weather_gov")


def json_default(value):
    if isinstance(value, bytes):
        return {"$base64": base64.b64encode(value).decode()}
    raise TypeError(type(value).__name__)


def verified_put(client, bucket, prefix, payload):
    digest = hashlib.sha256(payload).hexdigest()
    key = f"{prefix}/{digest}.json.gz"
    client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="application/gzip",
                      Metadata={"sha256": digest})
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        if body.read() != payload:
            raise RuntimeError("Remote readback differs; retain source database")
    finally:
        body.close()
    return {"key": key, "sha256": digest, "bytes": len(payload)}


def archive(path, client, bucket):
    manifest = {"database": str(path.resolve()), "tables": {}, "objects": []}
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN")
        schema = {r["name"]: r["sql"] for r in con.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table'")}
        for table in TABLES:
            if table not in schema:
                continue
            where, params = "", ()
            if table == "raw_snapshots":
                where, params = " WHERE source IN (?,?,?)", WEATHER
            cursor = con.execute(f'SELECT * FROM "{table}"{where}', params)
            count = 0
            while rows := cursor.fetchmany(100):
                payload = gzip.compress(json.dumps(
                    {"table": table, "rows": [dict(r) for r in rows]},
                    default=json_default, sort_keys=True).encode(), mtime=0)
                obj = verified_put(client, bucket, "legacy-weather/v1/rows", payload)
                manifest["objects"].append(obj | {"table": table, "rows": len(rows)})
                count += len(rows)
            manifest["tables"][table] = {"rows": count, "schema": schema[table]}
            print(json.dumps({"database": path.name, "table": table, "verified_rows": count}),
                  flush=True)
    payload = gzip.compress(json.dumps(manifest, sort_keys=True).encode(), mtime=0)
    manifest["remote_manifest"] = verified_put(
        client, bucket, "legacy-weather/v1/manifests", payload)
    return manifest


def main():
    import boto3

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    config = R2Config.from_env()
    client = boto3.client("s3", endpoint_url=config.endpoint_url,
                          aws_access_key_id=config.access_key_id,
                          aws_secret_access_key=config.secret_access_key, region_name="auto")
    result = archive(args.db, client, config.bucket)
    with args.manifest.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, sort_keys=True)


if __name__ == "__main__":
    main()

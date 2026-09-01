from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from nice_weather.config import CityConfig
from nice_weather.domain import stable_id, utc_now
from nice_weather.store import WeatherStore

WEATHER_EXPORT_TABLES = (
    "source_captures",
    "poll_attempts",
    "weather_observations",
    "weather_forecasts",
    "forecast_points",
    "settlement_evidence",
    "settlement_rows",
    "weather_feature_snapshots",
    "weather_daily_labels",
)


@dataclass(frozen=True)
class R2Config:
    endpoint_url: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    prefix: str = "nyc-klga/v2"

    @classmethod
    def from_env(cls) -> R2Config:
        names = (
            "R2_ENDPOINT_URL",
            "R2_BUCKET",
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
        )
        missing = [name for name in names if not os.environ.get(name)]
        if missing:
            raise RuntimeError(f"Missing R2 environment variables: {', '.join(missing)}")
        endpoint = os.environ["R2_ENDPOINT_URL"].rstrip("/")
        if not endpoint.startswith("https://"):
            raise RuntimeError("R2_ENDPOINT_URL must use HTTPS")
        return cls(
            endpoint_url=endpoint,
            bucket=os.environ["R2_BUCKET"],
            access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            prefix=os.environ.get("R2_PREFIX", "nyc-klga/v2").strip("/"),
        )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _quarter_hour(value: datetime) -> str:
    minute = value.minute - value.minute % 15
    return value.replace(minute=minute, second=0, microsecond=0).strftime("%Y%m%dT%H%MZ")


class R2Archive:
    def __init__(
        self,
        database_path: str | Path,
        city_config: CityConfig,
        r2_config: R2Config | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        self.database_path = str(database_path)
        self.city_config = city_config
        self.r2 = r2_config or R2Config.from_env()
        if client is None:
            try:
                import boto3
            except ImportError as exc:
                raise RuntimeError(
                    "R2 support requires the collector optional dependencies"
                ) from exc
            client = boto3.client(
                "s3",
                endpoint_url=self.r2.endpoint_url,
                aws_access_key_id=self.r2.access_key_id,
                aws_secret_access_key=self.r2.secret_access_key,
                region_name="auto",
            )
        self.client = client

    def _put(
        self,
        key: str,
        payload: bytes,
        *,
        content_type: str,
        content_encoding: str | None = None,
    ) -> None:
        arguments: dict[str, Any] = {
            "Bucket": self.r2.bucket,
            "Key": key,
            "Body": payload,
            "ContentType": content_type,
            "Metadata": {"sha256": _sha256(payload)},
        }
        if content_encoding:
            arguments["ContentEncoding"] = content_encoding
        self.client.put_object(**arguments)

    def _record_upload(
        self,
        *,
        export_type: str,
        source: str | None,
        local_date: str,
        key: str,
        payload: bytes,
        source_ids: list[str],
        error: Exception | None = None,
    ) -> None:
        now = utc_now()
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            store.record_r2_export(
                export_id=stable_id("r2_export", key),
                export_type=export_type,
                source=source,
                local_date=local_date,
                object_key=key,
                sha256=_sha256(payload),
                size_bytes=len(payload),
                source_ids=source_ids,
                created_at=now,
                uploaded_at=None if error else now,
                status="failed" if error else "uploaded",
                error=str(error) if error else None,
            )

    def sync_raw(self) -> list[str]:
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            rows = store.pending_source_captures()
        groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
        for row in rows:
            received_at = datetime.fromisoformat(row["received_at"])
            groups[(row["source"], row["local_date"], _quarter_hour(received_at))].append(row)
        uploaded = []
        for (source, local_date, interval), captures in groups.items():
            records = []
            for row in captures:
                records.append(
                    {
                        "schema_version": 2,
                        "capture_id": row["capture_id"],
                        "source": row["source"],
                        "kind": row["kind"],
                        "station_id": row["station_id"],
                        "requested_at": row["requested_at"],
                        "source_time": row["source_time"],
                        "observed_at": row["observed_at"],
                        "issued_at": row["issued_at"],
                        "received_at": row["received_at"],
                        "source_version": row["source_version"],
                        "content_hash": row["content_hash"],
                        "request_url": row["request_url"],
                        "http_status": row["http_status"],
                        "content_type": row["content_type"],
                        "content_encoding": row["content_encoding"],
                        "raw_base64": base64.b64encode(row["raw_blob"]).decode("ascii"),
                    }
                )
            ndjson = b"\n".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")
                for item in records
            )
            payload = gzip.compress(ndjson + b"\n", compresslevel=6, mtime=0)
            digest = _sha256(payload)
            key = (
                f"{self.r2.prefix}/raw/source={source}/local_date={local_date}/"
                f"{interval}_{digest}.jsonl.gz"
            )
            ids = [row["capture_id"] for row in captures]
            try:
                self._put(
                    key,
                    payload,
                    content_type="application/x-ndjson",
                    content_encoding="gzip",
                )
            except Exception as exc:
                self._record_upload(
                    export_type="raw",
                    source=source,
                    local_date=local_date,
                    key=key,
                    payload=payload,
                    source_ids=ids,
                    error=exc,
                )
                raise
            self._record_upload(
                export_type="raw",
                source=source,
                local_date=local_date,
                key=key,
                payload=payload,
                source_ids=ids,
            )
            uploaded.append(key)
        return uploaded

    def sync_screenshots(self) -> list[str]:
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            rows = store.pending_screenshots()
        uploaded = []
        for row in rows:
            payload = bytes(row["screenshot_png"])
            digest = _sha256(payload)
            received = datetime.fromisoformat(row["received_at"]).strftime("%Y%m%dT%H%M%SZ")
            key = (
                f"{self.r2.prefix}/evidence/local_date={row['local_date']}/"
                f"{received}_{digest}.png"
            )
            ids = [row["evidence_id"]]
            try:
                self._put(key, payload, content_type="image/png")
            except Exception as exc:
                self._record_upload(
                    export_type="evidence",
                    source="weather_gov",
                    local_date=row["local_date"],
                    key=key,
                    payload=payload,
                    source_ids=ids,
                    error=exc,
                )
                raise
            self._record_upload(
                export_type="evidence",
                source="weather_gov",
                local_date=row["local_date"],
                key=key,
                payload=payload,
                source_ids=ids,
            )
            uploaded.append(key)
        return uploaded

    def export_parquet(self, local_date: date) -> list[str]:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise RuntimeError(
                "Parquet export requires the collector optional dependencies"
            ) from exc
        local_date_text = local_date.isoformat()
        uploaded = []
        for table_name in WEATHER_EXPORT_TABLES:
            with WeatherStore(self.database_path) as store:
                store.init_schema()
                rows = store.rows_for_local_date(table_name, local_date_text)
            if not rows:
                continue
            arrow_table = pa.Table.from_pylist(rows)
            buffer = io.BytesIO()
            pq.write_table(arrow_table, buffer, compression="zstd")
            payload = buffer.getvalue()
            digest = _sha256(payload)
            key = (
                f"{self.r2.prefix}/parquet/table={table_name}/"
                f"year={local_date.year:04d}/month={local_date.month:02d}/"
                f"day={local_date.day:02d}/{digest}.parquet"
            )
            self._put(key, payload, content_type="application/vnd.apache.parquet")
            self._record_upload(
                export_type="parquet",
                source=table_name,
                local_date=local_date_text,
                key=key,
                payload=payload,
                source_ids=[f"parquet:{table_name}:{local_date_text}"],
            )
            uploaded.append(key)
        return uploaded

    def export_manifest(self, local_date: date) -> str:
        local_date_text = local_date.isoformat()
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            objects = store.r2_exports_for_day(local_date_text)
        manifest = {
            "schema_version": 2,
            "station_id": self.city_config.station_id,
            "timezone": self.city_config.timezone,
            "local_date": local_date_text,
            "created_at": utc_now().isoformat(),
            "objects": objects,
        }
        payload = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8")
        digest = _sha256(payload)
        key = f"{self.r2.prefix}/manifests/local_date={local_date_text}/{digest}.json"
        self._put(key, payload, content_type="application/json")
        self._record_upload(
            export_type="manifest",
            source=None,
            local_date=local_date_text,
            key=key,
            payload=payload,
            source_ids=[f"manifest:{local_date_text}"],
        )
        return key

    def sync(self, *, local_date: date | None = None, daily_if_due: bool = True) -> dict[str, Any]:
        raw = self.sync_raw()
        evidence = self.sync_screenshots()
        parquet: list[str] = []
        manifest = None
        export_day = local_date
        local_now = utc_now().astimezone(self.city_config.zone)
        due_at = time(
            self.city_config.collector.daily_export_hour,
            self.city_config.collector.daily_export_minute,
        )
        if export_day is None and daily_if_due and local_now.time() >= due_at:
            candidate = local_now.date() - timedelta(days=1)
            with WeatherStore(self.database_path) as store:
                store.init_schema()
                already_done = store.has_r2_export("manifest", candidate.isoformat())
            if not already_done:
                export_day = candidate
        if export_day is not None:
            parquet = self.export_parquet(export_day)
            manifest = self.export_manifest(export_day)
        return {"raw": raw, "evidence": evidence, "parquet": parquet, "manifest": manifest}

    def check(self) -> dict[str, Any]:
        now = utc_now()
        payload = json.dumps(
            {"station_id": self.city_config.station_id, "checked_at": now.isoformat()},
            sort_keys=True,
        ).encode("utf-8")
        digest = _sha256(payload)
        key = f"{self.r2.prefix}/healthchecks/{now:%Y%m%dT%H%M%SZ}_{digest}.json"
        self._put(key, payload, content_type="application/json")
        response = self.client.get_object(Bucket=self.r2.bucket, Key=key)
        downloaded = response["Body"].read()
        if downloaded != payload:
            raise RuntimeError("R2 healthcheck content mismatch")
        self._record_upload(
            export_type="healthcheck",
            source=None,
            local_date=now.astimezone(self.city_config.zone).date().isoformat(),
            key=key,
            payload=payload,
            source_ids=[f"healthcheck:{digest}"],
        )
        return {"ok": True, "bucket": self.r2.bucket, "object_key": key, "bytes": len(payload)}

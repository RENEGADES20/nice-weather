from __future__ import annotations

import hashlib
import inspect
import sqlite3
from collections.abc import Callable
from typing import Any

LATEST_SCHEMA_VERSION = 5


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        is not None
    )


def _add_column(
    connection: sqlite3.Connection, table: str, name: str, definition: str
) -> None:
    if name not in _columns(connection, table):
        connection.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition}')


def _migration_v4(connection: sqlite3.Connection) -> None:
    weather_columns = {
        "source": "TEXT NOT NULL DEFAULT 'aviationweather'",
        "temperature_c": "REAL",
        "raw_unit": "TEXT",
        "quality_control_json": "TEXT NOT NULL DEFAULT '{}'",
        "source_version": "TEXT",
        "revision": "INTEGER NOT NULL DEFAULT 1",
        "local_date": "TEXT",
        "provider_received_at": "TEXT",
        "report_time": "TEXT",
        "revision_type": "TEXT NOT NULL DEFAULT 'initial'",
        "parser_version": "TEXT NOT NULL DEFAULT 'legacy-v1'",
        "weather_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, definition in weather_columns.items():
        _add_column(connection, "weather_observations", name, definition)

    settlement_columns = {
        "parser_version": "TEXT NOT NULL DEFAULT 'legacy-v1'",
        "page_url": "TEXT",
        "content_hash": "TEXT",
        "screenshot_trigger": "TEXT",
        "response_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, definition in settlement_columns.items():
        _add_column(connection, "settlement_evidence", name, definition)

    _add_column(connection, "decision_outcomes", "quote_id", "TEXT")
    _add_column(connection, "paper_fills", "quote_id", "TEXT")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS poll_attempts (
          attempt_id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          kind TEXT NOT NULL,
          station_id TEXT NOT NULL,
          local_date TEXT NOT NULL,
          requested_at TEXT NOT NULL,
          received_at TEXT,
          http_status INTEGER,
          latency_ms INTEGER,
          succeeded INTEGER NOT NULL,
          content_changed INTEGER NOT NULL,
          capture_id TEXT REFERENCES source_captures(capture_id),
          content_hash TEXT,
          error_type TEXT,
          error_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_poll_attempts_source_time
          ON poll_attempts(source, kind, requested_at DESC);

        CREATE TABLE IF NOT EXISTS settlement_rows (
          row_id TEXT PRIMARY KEY,
          evidence_id TEXT NOT NULL REFERENCES settlement_evidence(evidence_id),
          capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
          station_id TEXT NOT NULL,
          local_date TEXT NOT NULL,
          observed_at TEXT NOT NULL,
          received_at TEXT NOT NULL,
          temperature_f REAL NOT NULL,
          row_index INTEGER NOT NULL,
          row_hash TEXT NOT NULL,
          UNIQUE(evidence_id, row_index)
        );

        CREATE TABLE IF NOT EXISTS weather_feature_snapshots (
          feature_snapshot_id TEXT PRIMARY KEY,
          station_id TEXT NOT NULL,
          local_date TEXT NOT NULL,
          decision_time TEXT NOT NULL,
          feature_schema_version TEXT NOT NULL,
          input_capture_ids_json TEXT NOT NULL,
          input_set_hash TEXT NOT NULL,
          forecast_tmax_f REAL,
          official_hourly_tmax_f REAL,
          nws_observed_tmax_f REAL,
          metar_observed_tmax_f REAL,
          features_json TEXT NOT NULL,
          missing_flags_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS weather_daily_labels (
          label_id TEXT PRIMARY KEY,
          station_id TEXT NOT NULL,
          local_date TEXT NOT NULL,
          official_tmax_f REAL NOT NULL,
          evidence_id TEXT NOT NULL REFERENCES settlement_evidence(evidence_id),
          finalized_at TEXT NOT NULL,
          label_version TEXT NOT NULL,
          label_hash TEXT NOT NULL,
          UNIQUE(station_id, local_date, label_version)
        );

        CREATE TABLE IF NOT EXISTS model_predictions (
          prediction_id TEXT PRIMARY KEY,
          feature_snapshot_id TEXT NOT NULL
            REFERENCES weather_feature_snapshots(feature_snapshot_id),
          decision_id TEXT,
          model_version TEXT NOT NULL,
          generated_at TEXT NOT NULL,
          mean_tmax_f REAL NOT NULL,
          probability_sum REAL NOT NULL,
          probabilities_json TEXT NOT NULL,
          status TEXT NOT NULL,
          no_trade_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS decision_weather_inputs (
          decision_id TEXT NOT NULL REFERENCES decisions(decision_id),
          capture_id TEXT NOT NULL REFERENCES source_captures(capture_id),
          role TEXT NOT NULL,
          PRIMARY KEY(decision_id, capture_id, role)
        );

        CREATE TABLE IF NOT EXISTS market_captures (
          capture_id TEXT PRIMARY KEY,
          source TEXT NOT NULL,
          kind TEXT NOT NULL,
          event_id TEXT,
          market_id TEXT,
          requested_at TEXT NOT NULL,
          received_at TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          UNIQUE(source, kind, content_hash)
        );

        CREATE TABLE IF NOT EXISTS execution_quotes (
          quote_id TEXT PRIMARY KEY,
          snapshot_id TEXT NOT NULL REFERENCES raw_snapshots(snapshot_id),
          decision_id TEXT,
          market_id TEXT NOT NULL,
          token_id TEXT NOT NULL,
          requested_at TEXT NOT NULL,
          received_at TEXT NOT NULL,
          book_hash TEXT NOT NULL,
          best_bid REAL,
          best_ask REAL,
          spread REAL,
          target_quantity REAL NOT NULL,
          bid_vwap REAL,
          ask_vwap REAL,
          bid_depth REAL NOT NULL,
          ask_depth REAL NOT NULL,
          top_levels_json TEXT NOT NULL,
          status TEXT NOT NULL,
          error_reason TEXT,
          UNIQUE(snapshot_id)
        );
        CREATE INDEX IF NOT EXISTS idx_execution_quotes_token_time
          ON execution_quotes(token_id, received_at DESC);
        """
    )


def _migration_v5(connection: sqlite3.Connection) -> None:
    weather_columns = {
        "source": "TEXT NOT NULL DEFAULT 'aviationweather'",
        "temperature_c": "REAL",
        "raw_unit": "TEXT",
        "quality_control_json": "TEXT NOT NULL DEFAULT '{}'",
        "source_version": "TEXT",
        "revision": "INTEGER NOT NULL DEFAULT 1",
        "local_date": "TEXT",
        "provider_received_at": "TEXT",
        "report_time": "TEXT",
        "revision_type": "TEXT NOT NULL DEFAULT 'initial'",
        "parser_version": "TEXT NOT NULL DEFAULT 'legacy-v1'",
        "weather_metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, definition in weather_columns.items():
        _add_column(connection, "weather_observations", name, definition)

    if "capture_id" not in _columns(connection, "weather_observations"):
        connection.executescript(
            """
            ALTER TABLE weather_observations RENAME TO weather_observations_v4;
            CREATE TABLE weather_observations (
              observation_id TEXT PRIMARY KEY,
              capture_id TEXT REFERENCES source_captures(capture_id),
              legacy_snapshot_id TEXT REFERENCES raw_snapshots(snapshot_id),
              station_id TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              received_at TEXT NOT NULL,
              temperature_f REAL NOT NULL,
              raw_text TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'aviationweather',
              temperature_c REAL,
              raw_unit TEXT,
              quality_control_json TEXT NOT NULL DEFAULT '{}',
              source_version TEXT,
              revision INTEGER NOT NULL DEFAULT 1,
              local_date TEXT,
              provider_received_at TEXT,
              report_time TEXT,
              revision_type TEXT NOT NULL DEFAULT 'initial',
              parser_version TEXT NOT NULL DEFAULT 'legacy-v1',
              weather_metadata_json TEXT NOT NULL DEFAULT '{}',
              CHECK(capture_id IS NOT NULL OR legacy_snapshot_id IS NOT NULL)
            );
            INSERT INTO weather_observations(
              observation_id,capture_id,legacy_snapshot_id,station_id,observed_at,
              received_at,temperature_f,raw_text,source,temperature_c,raw_unit,
              quality_control_json,source_version,revision,local_date,
              provider_received_at,report_time,revision_type,parser_version,
              weather_metadata_json
            )
            SELECT
              observation_id,
              CASE WHEN EXISTS(
                SELECT 1 FROM source_captures
                WHERE capture_id=weather_observations_v4.snapshot_id
              ) THEN snapshot_id END,
              CASE WHEN EXISTS(
                SELECT 1 FROM source_captures
                WHERE capture_id=weather_observations_v4.snapshot_id
              ) THEN NULL ELSE snapshot_id END,
              station_id,observed_at,received_at,temperature_f,raw_text,source,
              temperature_c,raw_unit,quality_control_json,source_version,revision,
              local_date,provider_received_at,report_time,revision_type,
              parser_version,weather_metadata_json
            FROM weather_observations_v4;
            DROP TABLE weather_observations_v4;
            CREATE INDEX idx_weather_observations_capture
              ON weather_observations(capture_id);
            """
        )

    if "capture_id" not in _columns(connection, "forecast_points"):
        connection.executescript(
            """
            ALTER TABLE forecast_points RENAME TO forecast_points_v4;
            CREATE TABLE forecast_points (
              forecast_point_id TEXT PRIMARY KEY,
              capture_id TEXT REFERENCES source_captures(capture_id),
              legacy_snapshot_id TEXT REFERENCES raw_snapshots(snapshot_id),
              source TEXT NOT NULL,
              issued_at TEXT NOT NULL,
              valid_at TEXT NOT NULL,
              received_at TEXT NOT NULL,
              temperature_f REAL NOT NULL,
              CHECK(capture_id IS NOT NULL OR legacy_snapshot_id IS NOT NULL)
            );
            INSERT INTO forecast_points(
              forecast_point_id,capture_id,legacy_snapshot_id,source,issued_at,
              valid_at,received_at,temperature_f
            )
            SELECT
              forecast_point_id,
              CASE WHEN EXISTS(
                SELECT 1 FROM source_captures
                WHERE capture_id=forecast_points_v4.snapshot_id
              ) THEN snapshot_id END,
              CASE WHEN EXISTS(
                SELECT 1 FROM source_captures
                WHERE capture_id=forecast_points_v4.snapshot_id
              ) THEN NULL ELSE snapshot_id END,
              source,issued_at,valid_at,received_at,temperature_f
            FROM forecast_points_v4;
            DROP TABLE forecast_points_v4;
            CREATE INDEX idx_forecast_points_capture ON forecast_points(capture_id);
            """
        )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_weather_observations_capture "
        "ON weather_observations(capture_id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_forecast_points_capture "
        "ON forecast_points(capture_id)"
    )
    if _table_exists(connection, "decision_inputs") and _table_exists(
        connection, "decision_weather_inputs"
    ):
        connection.execute(
            """
            INSERT OR IGNORE INTO decision_weather_inputs(decision_id,capture_id,role)
            SELECT input.decision_id,input.snapshot_id,'weather_as_of'
            FROM decision_inputs AS input
            JOIN source_captures AS capture ON capture.capture_id=input.snapshot_id
            """
        )
        connection.execute(
            """
            DELETE FROM decision_inputs
            WHERE snapshot_id IN (SELECT capture_id FROM source_captures)
            """
        )


MIGRATIONS: tuple[tuple[int, str, Callable[[sqlite3.Connection], None]], ...] = (
    (4, "unified_weather_store", _migration_v4),
    (5, "source_capture_ownership", _migration_v5),
)


def apply_migrations(connection: sqlite3.Connection) -> int:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
          version INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          checksum TEXT NOT NULL
        )
        """
    )
    existing = {
        int(row[0]): str(row[1])
        for row in connection.execute("SELECT version, checksum FROM schema_migrations")
    }
    for version, name, operation in MIGRATIONS:
        checksum = hashlib.sha256(
            f"{version}:{name}:{inspect.getsource(operation)}".encode()
        ).hexdigest()
        if version in existing:
            if existing[version] != checksum:
                raise RuntimeError(f"Migration checksum mismatch for version {version}")
            continue
        operation(connection)
        connection.execute(
            "INSERT INTO schema_migrations(version,name,checksum) VALUES(?,?,?)",
            (version, name, checksum),
        )
    connection.execute("UPDATE schema_meta SET version=?", (LATEST_SCHEMA_VERSION,))
    return LATEST_SCHEMA_VERSION


def verify_database(connection: sqlite3.Connection) -> dict[str, Any]:
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_keys = [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]
    version = int(connection.execute("SELECT version FROM schema_meta").fetchone()[0])
    migrations = [
        dict(zip(("version", "name", "applied_at", "checksum"), row, strict=True))
        for row in connection.execute(
            "SELECT version,name,applied_at,checksum FROM schema_migrations ORDER BY version"
        )
    ]
    return {
        "ok": integrity == "ok" and not foreign_keys and version == LATEST_SCHEMA_VERSION,
        "schema_version": version,
        "latest_schema_version": LATEST_SCHEMA_VERSION,
        "integrity_check": integrity,
        "foreign_key_errors": foreign_keys,
        "migrations": migrations,
    }

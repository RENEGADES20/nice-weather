import sqlite3

from nice_weather.migrations import _migration_v5


def test_v5_migration_preserves_legacy_and_promotes_source_captures() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA foreign_keys=ON;
        CREATE TABLE raw_snapshots(snapshot_id TEXT PRIMARY KEY);
        CREATE TABLE source_captures(capture_id TEXT PRIMARY KEY);
        INSERT INTO raw_snapshots VALUES('captured'),('legacy');
        INSERT INTO source_captures VALUES('captured');
        CREATE TABLE weather_observations(
          observation_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL,
          station_id TEXT NOT NULL, observed_at TEXT NOT NULL, received_at TEXT NOT NULL,
          temperature_f REAL NOT NULL, raw_text TEXT NOT NULL
        );
        INSERT INTO weather_observations(
          observation_id,snapshot_id,station_id,observed_at,received_at,
          temperature_f,raw_text
        ) VALUES
          ('obs-captured','captured','KLGA','2026-09-01T12:00:00+00:00',
           '2026-09-01T12:01:00+00:00',70,''),
          ('obs-legacy','legacy','KLGA','2026-08-01T12:00:00+00:00',
           '2026-08-01T12:01:00+00:00',71,'');
        CREATE TABLE forecast_points(
          forecast_point_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL,
          source TEXT NOT NULL, issued_at TEXT NOT NULL, valid_at TEXT NOT NULL,
          received_at TEXT NOT NULL, temperature_f REAL NOT NULL
        );
        INSERT INTO forecast_points VALUES(
          'forecast-captured','captured','nws','2026-09-01T10:00:00+00:00',
          '2026-09-01T12:00:00+00:00','2026-09-01T10:01:00+00:00',72
        );
        CREATE TABLE decision_inputs(
          decision_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, role TEXT NOT NULL,
          PRIMARY KEY(decision_id,snapshot_id,role)
        );
        CREATE TABLE decision_weather_inputs(
          decision_id TEXT NOT NULL, capture_id TEXT NOT NULL, role TEXT NOT NULL,
          PRIMARY KEY(decision_id,capture_id,role)
        );
        INSERT INTO decision_inputs VALUES
          ('decision','captured','decision_state'),
          ('decision','legacy','decision_state');
        """
    )

    _migration_v5(connection)

    observations = {
        row["observation_id"]: (row["capture_id"], row["legacy_snapshot_id"])
        for row in connection.execute("SELECT * FROM weather_observations")
    }
    assert observations["obs-captured"] == ("captured", None)
    assert observations["obs-legacy"] == (None, "legacy")
    forecast = connection.execute("SELECT * FROM forecast_points").fetchone()
    assert (forecast["capture_id"], forecast["legacy_snapshot_id"]) == ("captured", None)
    remaining_input = connection.execute(
        "SELECT snapshot_id FROM decision_inputs"
    ).fetchone()
    assert remaining_input[0] == "legacy"
    weather_input = connection.execute("SELECT * FROM decision_weather_inputs").fetchone()
    assert (weather_input["capture_id"], weather_input["role"]) == (
        "captured",
        "weather_as_of",
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

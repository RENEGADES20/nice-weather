from __future__ import annotations

from nice_weather.queries import DashboardQuery
from nice_weather.runner import run_fixture_once
from nice_weather.store import WeatherStore


def test_fixture_e2e_is_deterministic_and_queryable(fixture_manifest, tmp_path) -> None:
    database = tmp_path / "fixture.sqlite3"
    first = run_fixture_once(fixture_manifest, database)
    second = run_fixture_once(fixture_manifest, database)

    assert first.decision_id == second.decision_id
    assert [item.model_probability for item in first.outcomes] == [
        item.model_probability for item in second.outcomes
    ]
    query = DashboardQuery(database)
    summary = query.get_latest_decision_summary()
    assert summary is not None
    assert summary["decision_id"] == first.decision_id
    outcomes = query.get_outcome_snapshot(first.decision_id)
    assert len(outcomes) == 11
    probabilities = query.get_latest_event_probabilities(summary["event_id"])
    assert len(probabilities) == 11
    quoted = next(item for item in outcomes if item["quote_id"] is not None)
    quote = query.get_execution_quote(first.decision_id, quoted["bin_id"])
    assert quote is not None
    assert quote["quote_id"] == quoted["quote_id"]
    weather = query.get_weather_path(first.decision_id)
    assert len(weather["forecasts"]) == 24
    assert len(weather["observations"]) == 0
    assert len(query.get_decision_trace(first.decision_id)) == 15

    with WeatherStore(database, read_only=True) as store:
        counts = store.table_counts()
    assert counts["decisions"] == 1
    assert counts["raw_snapshots"] == 15
    assert counts["decision_inputs"] == 15
    assert counts["paper_fills"] == 1
    assert counts["paper_orders"] == 1

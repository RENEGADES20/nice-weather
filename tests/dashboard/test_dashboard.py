from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.dashboard import (
    _age,
    _default_timeline_bins,
    _display_timezone,
    _format_timestamp,
    _localize_record,
)
from nice_weather.domain import RunMode
from nice_weather.runner import _run_bundle, run_fixture_once
from nice_weather.store import WeatherStore

DASHBOARD = Path(__file__).resolve().parents[2] / "src" / "nice_weather" / "dashboard.py"


def test_dashboard_renders_fixture(fixture_manifest, tmp_path, monkeypatch) -> None:
    database = tmp_path / "fixture.sqlite3"
    run_fixture_once(fixture_manifest, database)
    monkeypatch.setenv("NICE_WEATHER_DB", str(database))

    app = AppTest.from_file(DASHBOARD, default_timeout=20).run()

    assert not app.exception
    assert app.title[0].value == "Polymarket NYC / KLGA Trader Dashboard"
    assert [tab.label for tab in app.tabs] == [
        "Overview",
        "Repricing",
        "Execution",
        "Paper",
        "System & Audit",
    ]
    assert any("Build" in caption.value for caption in app.caption)
    assert any(metric.label == "Quote age" for metric in app.metric)
    assert any("dashboard-status" in item.value for item in app.markdown)
    subheaders = [item.value for item in app.subheader]
    assert "KLGA Tmax and market repricing" in subheaders
    assert "Executable quote" in subheaders
    assert "Market Detail" not in subheaders


def test_dashboard_handles_empty_database(tmp_path, monkeypatch) -> None:
    database = tmp_path / "empty.sqlite3"
    with WeatherStore(database) as store:
        store.init_schema()
    monkeypatch.setenv("NICE_WEATHER_DB", str(database))

    app = AppTest.from_file(DASHBOARD, default_timeout=20).run()

    assert not app.exception
    assert any("No completed decision" in item.value for item in app.info)


def test_dashboard_renders_no_trade_coverage_gap(fixture_manifest, tmp_path, monkeypatch) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    database = tmp_path / "no-trade.sqlite3"
    _run_bundle(replace(bundle, forecasts=bundle.forecasts[:-1]), database, config, RunMode.FIXTURE)
    monkeypatch.setenv("NICE_WEATHER_DB", str(database))

    app = AppTest.from_file(DASHBOARD, default_timeout=20).run()

    assert not app.exception
    assert any("DATA_FORECAST_COVERAGE_GAP" in item.value for item in app.warning)


def test_dashboard_formats_utc_in_browser_timezone() -> None:
    zone, timezone_name = _display_timezone("America/Chicago")

    assert timezone_name == "America/Chicago"
    assert _format_timestamp("2026-09-02T15:30:00+00:00", zone) == "2026-09-02 10:30:00 CDT"
    assert _format_timestamp("2026-01-02T15:30:00Z", zone) == "2026-01-02 09:30:00 CST"


def test_dashboard_localizes_timestamp_fields_without_changing_market_day() -> None:
    record = {
        "decision_time": "2026-09-02T15:30:00+00:00",
        "received_at": "2026-09-02T15:31:00+00:00",
        "local_date": "2026-09-02",
        "nested": {"valid_from": "2026-09-02T16:00:00+00:00"},
    }

    localized = _localize_record(record, ZoneInfo("America/Los_Angeles"))

    assert localized["decision_time"] == "2026-09-02 08:30:00 PDT"
    assert localized["received_at"] == "2026-09-02 08:31:00 PDT"
    assert localized["local_date"] == "2026-09-02"
    assert localized["nested"]["valid_from"] == "2026-09-02 09:00:00 PDT"


def test_default_timeline_bins_follow_tmax_then_model_probability() -> None:
    bins = [
        {"bin_id": "low", "lower_bound": None, "upper_bound": 79},
        {"bin_id": "current", "lower_bound": 80, "upper_bound": 81},
        {"bin_id": "high", "lower_bound": 82, "upper_bound": 83},
    ]

    assert _default_timeline_bins(bins, 80.4, {"high": 0.8}) == ["current", "high"]
    assert _default_timeline_bins(bins, 79.6, {"high": 0.8}) == ["current", "high"]
    assert _default_timeline_bins(bins, None, {"current": 0.2, "high": 0.8}) == ["high"]


def test_quote_age_uses_readable_units() -> None:
    assert _age(None) == "Unavailable"
    assert _age(12.4) == "12s"
    assert _age(125) == "2m 5s"
    assert _age(7_500) == "2h 5m"
    assert _age(183_600) == "2d 3h"

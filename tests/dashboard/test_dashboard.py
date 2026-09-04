from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.dashboard import (
    _age,
    _browser_timezone_note,
    _default_timeline_bin,
    _display_timezone,
    _format_timestamp,
    _localize_record,
    _resolution_source_matches,
    _select_price,
    _series_delta,
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


def test_dashboard_formats_all_times_in_new_york() -> None:
    zone, timezone_name = _display_timezone("America/Chicago")

    assert timezone_name == "ET"
    assert _format_timestamp("2026-09-02T15:30:00+00:00", zone) == "2026-09-02 11:30:00 ET"
    assert _format_timestamp("2026-01-02T15:30:00Z", zone) == "2026-01-02 10:30:00 ET"


def test_browser_timezone_note_uses_instantaneous_dst_offsets() -> None:
    assert (
        _browser_timezone_note("America/Chicago", datetime(2026, 7, 1, 12, tzinfo=UTC))
        == "Browser: America/Chicago · New York +1h"
    )
    assert (
        _browser_timezone_note("America/Phoenix", datetime(2026, 1, 1, 12, tzinfo=UTC))
        == "Browser: America/Phoenix · New York +2h"
    )
    assert _browser_timezone_note("America/New_York") == ("Browser: America/New_York · Same time")
    assert _browser_timezone_note("Invalid/Zone") == "Browser timezone unavailable"


def test_dashboard_localizes_timestamp_fields_without_changing_market_day() -> None:
    record = {
        "decision_time": "2026-09-02T15:30:00+00:00",
        "received_at": "2026-09-02T15:31:00+00:00",
        "local_date": "2026-09-02",
        "nested": {"valid_from": "2026-09-02T16:00:00+00:00"},
    }

    localized = _localize_record(record, ZoneInfo("America/New_York"))

    assert localized["decision_time"] == "2026-09-02 11:30:00 ET"
    assert localized["received_at"] == "2026-09-02 11:31:00 ET"
    assert localized["local_date"] == "2026-09-02"
    assert localized["nested"]["valid_from"] == "2026-09-02 12:00:00 ET"


def test_default_timeline_bin_follows_tmax_then_model_probability() -> None:
    bins = [
        {"bin_id": "low", "lower_bound": None, "upper_bound": 79},
        {"bin_id": "current", "lower_bound": 80, "upper_bound": 81},
        {"bin_id": "high", "lower_bound": 82, "upper_bound": 83},
    ]

    assert _default_timeline_bin(bins, 80.4, {"high": 0.8}) == "current"
    assert _default_timeline_bin(bins, 79.6, {"high": 0.8}) == "current"
    assert _default_timeline_bin(bins, None, {"current": 0.2, "high": 0.8}) == "high"


def test_resolution_source_match_is_dynamic_and_case_insensitive() -> None:
    assert _resolution_source_matches(
        "https://www.weather.gov/wrh/timeseries?site=klga",
        "https://www.weather.gov/wrh/timeseries?site=KLGA",
    )
    assert not _resolution_source_matches(
        "https://example.com/settlement", "https://www.weather.gov/wrh/timeseries?site=KLGA"
    )


def _tick(
    when: datetime,
    *,
    tick_id: str,
    source: str = "clob_ws",
    status: str = "available",
    bid: float | None = None,
    ask: float | None = None,
    mid: float | None = None,
    trade: float | None = None,
) -> dict[str, object]:
    return {
        "tick_id": tick_id,
        "exchange_event_at": when.isoformat(),
        "received_at": when.isoformat(),
        "source": source,
        "status": status,
        "best_bid": bid,
        "best_ask": ask,
        "mid": mid,
        "last_trade_price": trade,
    }


def test_price_fallback_prefers_valid_clob_then_recent_trade_then_gamma() -> None:
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    gamma = _tick(
        now - timedelta(minutes=9),
        tick_id="g",
        source="gamma_fallback",
        bid=0.3,
        ask=0.4,
        mid=0.35,
    )
    trade = _tick(now - timedelta(minutes=4), tick_id="t", trade=0.42)
    clob = _tick(now - timedelta(seconds=20), tick_id="c", bid=0.4, ask=0.5, mid=0.45)
    assert _select_price([gamma, trade, clob], now)["source"] == "CLOB mid"
    crossed = _tick(
        now - timedelta(seconds=10),
        tick_id="x",
        status="crossed",
        bid=0.6,
        ask=0.5,
        mid=None,
        trade=0.42,
    )
    assert _select_price([gamma, trade, crossed], now)["source"] == "Last trade"
    assert _select_price([gamma], now)["source"] == "Gamma approximate"
    old_gamma = _tick(
        now - timedelta(minutes=11),
        tick_id="old",
        source="gamma_fallback",
        mid=0.3,
    )
    assert _select_price([old_gamma], now) is None


def test_repricing_feed_only_emits_new_or_revised_points() -> None:
    initial = [{"id": "price", "name": "Price", "points": [{"time": 60, "value": 0.4}]}]
    first, hashes = _series_delta(initial, {})
    assert first[0]["points"] == [{"time": 60, "value": 0.4}]
    next_series = [
        {
            "id": "price",
            "name": "Price",
            "points": [{"time": 60, "value": 0.42}, {"time": 120, "value": 0.5}],
        }
    ]
    delta, _ = _series_delta(next_series, hashes)
    assert delta[0]["points"] == [
        {"time": 60, "value": 0.42},
        {"time": 120, "value": 0.5},
    ]


def test_quote_age_uses_readable_units() -> None:
    assert _age(None) == "Unavailable"
    assert _age(12.4) == "12s"
    assert _age(125) == "2m 5s"
    assert _age(7_500) == "2h 5m"
    assert _age(183_600) == "2d 3h"

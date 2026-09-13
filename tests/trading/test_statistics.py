from datetime import UTC, datetime, timedelta

from nice_weather.trading.metrics import daily_statistics


def test_sharpe_requires_complete_days_and_handles_zero_variance():
    start = datetime(2026, 1, 1, 5, tzinfo=UTC)
    series = [
        {
            "ts": int((start + timedelta(minutes=5 * i)).timestamp() * 1e9),
            "equity": 100 + i // 288 + (i // 288) % 2,
        }
        for i in range(35 * 288)
    ]
    assert daily_statistics(series[: 30 * 288])["sharpe"] is None
    assert daily_statistics(series[::288])["sharpe"] is None
    assert daily_statistics(series)["sharpe"] is not None
    assert daily_statistics([r | {"equity": 100} for r in series])["sharpe"] is None
    series[3 * 288]["equity"] = None
    assert not daily_statistics(series)["days"][3]["complete"]

from datetime import UTC, datetime

import pytest

from nice_weather.trading.feed import FeedStore
from nice_weather.trading.knyc_model import features, forest_predict


def test_first_receipt_does_not_refresh_on_repoll(tmp_path):
    store = FeedStore(tmp_path / "feed.sqlite3")
    row = {"icaoId": "KNYC", "obsTime": 10, "temp": 20}
    assert store.observation_receipts([row], 20)[0]["first_received_at"] == 20
    store = FeedStore(store.path)
    assert store.observation_receipts([row], 1000)[0]["first_received_at"] == 20


def test_features_require_day_coverage_and_received_forecast():
    now = datetime(2026, 9, 19, 19, tzinfo=UTC).timestamp()
    start = datetime(2026, 9, 19, 5, tzinfo=UTC).timestamp()
    obs = {start + h * 3600: 70 - h * 0.1 for h in range(15)}
    hrrr = {
        "complete": True,
        "received_at": now - 1,
        "cycle": now - 7200,
        "points": [
            {"valid_at": now + h * 3600, "temperature_f": 65, "received_at": now - 1}
            for h in range(1, 11)
        ],
    }
    row, previous, latest = features(obs, now, hrrr)
    assert row["observed_proxy_high"] == previous == 70 and latest == now
    assert row["hrrr_remaining"] == -5
    with pytest.raises(ValueError, match="INCOMPLETE_CLIMATE_DAY"):
        features({now: 70}, now, hrrr)
    with pytest.raises(ValueError, match="STALE_OR_INCOMPLETE_HRRR"):
        features(obs, now, hrrr | {"received_at": now + 1})


def test_exported_forest_preserves_leaf_distribution():
    forest = {
        "classes": [0, 1],
        "trees": [
            {
                "left": [1, -1, -1],
                "right": [2, -1, -1],
                "feature": [0, -2, -2],
                "threshold": [1, -2, -2],
                "value": [[2, 2], [1, 3], [3, 1]],
            }
        ],
    }
    assert forest_predict(forest, [1]) == [0.25, 0.75]
    assert forest_predict(forest, [2]) == [0.75, 0.25]

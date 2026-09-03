from datetime import UTC, datetime

from nice_weather.config import load_city_config
from nice_weather.deployment import deployment_health
from nice_weather.domain import MarketTopTick
from nice_weather.store import WeatherStore


def test_deployment_health_validates_market_object_date(tmp_path) -> None:
    database = tmp_path / "deployment.sqlite3"
    with WeatherStore(database) as store:
        store.init_schema()
        store.save_market_top_tick(
            MarketTopTick(
                tick_id="tick",
                event_id="event",
                condition_id="condition",
                market_id="market",
                bin_id="bin",
                token_id="token",
                label="80 F",
                exchange_event_at=datetime(2026, 9, 4, 3, 30, tzinfo=UTC),
                received_at=datetime(2026, 9, 4, 3, 30, 1, tzinfo=UTC),
                object_timezone="America/New_York",
                object_local_date=datetime(2026, 9, 3, tzinfo=UTC).date(),
                source="clob_ws",
                status="available",
                event_hash="hash",
                best_bid=0.4,
                best_ask=0.5,
                mid=0.45,
            )
        )

    result = deployment_health(
        database, load_city_config(), now=datetime(2026, 9, 4, 4, tzinfo=UTC)
    )

    assert result["ok"]
    assert result["bad_market_dates"] == 0

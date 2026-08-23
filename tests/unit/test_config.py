from nice_weather.config import load_city_config


def test_nyc_klga_config_is_fixed_to_mvp_scope() -> None:
    config = load_city_config()
    assert config.city_code == "NYC"
    assert config.station_id == "KLGA"
    assert config.timezone == "America/New_York"
    assert config.allowed_units == ("F",)
    assert config.model.sigma_f == 3.0


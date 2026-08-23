from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, date, datetime

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.contract import parse_gamma_contract
from nice_weather.reason_codes import ReasonCode
from nice_weather.runner import _fixture_health


def test_new_york_dst_local_day_has_correct_utc_window() -> None:
    zone = load_city_config().zone
    spring_start = datetime.combine(date(2026, 3, 8), datetime.min.time(), zone)
    spring_end = datetime.combine(date(2026, 3, 9), datetime.min.time(), zone)
    fall_start = datetime.combine(date(2026, 11, 1), datetime.min.time(), zone)
    fall_end = datetime.combine(date(2026, 11, 2), datetime.min.time(), zone)

    assert (spring_end.astimezone(UTC) - spring_start.astimezone(UTC)).total_seconds() == 23 * 3600
    assert (fall_end.astimezone(UTC) - fall_start.astimezone(UTC)).total_seconds() == 25 * 3600


def test_station_rule_ambiguity_is_blocking(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    payload = copy.deepcopy(bundle.gamma_snapshot.payload)
    event = payload["events"][0]
    event["description"] = event["description"].replace("LaGuardia", "unspecified airport")
    event["description"] = event["description"].replace("klga", "unknown")
    contract = parse_gamma_contract(payload, config)

    assert not contract.tradable
    assert ReasonCode.RULE_STATION_AMBIGUOUS in contract.ambiguities


def test_stale_metar_source_time_blocks_decision(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    stale_observations = tuple(
        replace(item, observed_at=item.observed_at.replace(year=2025))
        for item in bundle.observations
    )
    stale_bundle = replace(bundle, observations=stale_observations)
    health = _fixture_health(stale_bundle, contract, config)

    assert health.level.value == "BLOCKED"
    assert ReasonCode.DATA_OBSERVATION_STALE in health.reason_codes

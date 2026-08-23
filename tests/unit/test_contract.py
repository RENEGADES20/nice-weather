from __future__ import annotations

import shutil
from datetime import date

import pytest

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.contract import parse_gamma_contract


def test_real_fixture_maps_klga_contract(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)

    assert contract.tradable
    assert contract.event_id == "892623"
    assert contract.station_id == "KLGA"
    assert contract.local_day == date(2026, 8, 24)
    assert contract.unit == "F"
    assert contract.rounding == "source_whole_degree"
    assert len(contract.bins) == 11
    assert contract.bins[0].lower_bound is None
    assert contract.bins[0].upper_bound == 67
    assert contract.bins[-1].lower_bound == 86
    assert contract.bins[-1].upper_bound is None
    assert contract.observation_start.isoformat() == "2026-08-24T04:00:00+00:00"
    assert contract.observation_end.isoformat() == "2026-08-25T04:00:00+00:00"


def test_fixture_hashes_are_enforced(fixture_manifest, tmp_path) -> None:
    config = load_city_config()
    broken_dir = tmp_path / "fixture"
    shutil.copytree(fixture_manifest.parent, broken_dir)
    gamma_path = broken_dir / "gamma_event.json"
    gamma_path.write_text(gamma_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Fixture hash mismatch"):
        load_fixture(broken_dir / "manifest.json", config)

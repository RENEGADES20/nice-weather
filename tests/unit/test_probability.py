from __future__ import annotations

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.contract import parse_gamma_contract
from nice_weather.probability import estimate_tmax


def test_probability_partition_sums_to_one(fixture_manifest) -> None:
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    estimate = estimate_tmax(
        contract,
        forecast_tmax_f=80,
        observed_tmax_f=71,
        sigma_f=3,
        generated_at=bundle.decision_time,
        input_snapshot_ids=tuple(snapshot.snapshot_id for snapshot in bundle.snapshots),
        model_version="baseline-normal-v1",
    )

    assert abs(estimate.probability_sum - 1) < 1e-12
    assert all(0 <= item.probability <= 1 for item in estimate.probabilities)
    assert max(estimate.probabilities, key=lambda item: item.probability).bin_id in {
        contract.bins[6].bin_id,
        contract.bins[7].bin_id,
    }

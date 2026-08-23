from __future__ import annotations

import math
from datetime import datetime

from nice_weather.domain import BinProbability, MarketContract, ProbabilityEstimate


class ProbabilityError(ValueError):
    pass


def _normal_cdf(value: float, mean: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((value - mean) / (sigma * math.sqrt(2.0))))


def estimate_tmax(
    contract: MarketContract,
    forecast_tmax_f: float,
    observed_tmax_f: float | None,
    sigma_f: float,
    generated_at: datetime,
    input_snapshot_ids: tuple[str, ...],
    model_version: str,
) -> ProbabilityEstimate:
    if sigma_f <= 0 or not math.isfinite(forecast_tmax_f):
        raise ProbabilityError("Forecast Tmax and sigma must be finite and sigma must be positive")
    mean = max(forecast_tmax_f, observed_tmax_f) if observed_tmax_f is not None else forecast_tmax_f
    probabilities: list[BinProbability] = []
    for item in contract.bins:
        low = -math.inf if item.lower_bound is None else item.lower_bound - 0.5
        high = math.inf if item.upper_bound is None else item.upper_bound + 0.5
        lower_cdf = 0.0 if low == -math.inf else _normal_cdf(low, mean, sigma_f)
        upper_cdf = 1.0 if high == math.inf else _normal_cdf(high, mean, sigma_f)
        probability = upper_cdf - lower_cdf
        if not 0.0 <= probability <= 1.0:
            raise ProbabilityError(f"Invalid probability for {item.label}: {probability}")
        probabilities.append(BinProbability(item.bin_id, probability))
    probability_sum = sum(item.probability for item in probabilities)
    if abs(probability_sum - 1.0) > 1e-6:
        raise ProbabilityError(f"Probability sum is {probability_sum}, expected 1")
    z80 = 1.2815515655446004
    return ProbabilityEstimate(
        model_version=model_version,
        generated_at=generated_at,
        baseline_tmax_f=forecast_tmax_f,
        observed_tmax_f=observed_tmax_f,
        mean_tmax_f=mean,
        median_tmax_f=mean,
        interval_low_f=mean - z80 * sigma_f,
        interval_high_f=mean + z80 * sigma_f,
        probabilities=tuple(probabilities),
        probability_sum=probability_sum,
        input_snapshot_ids=input_snapshot_ids,
    )

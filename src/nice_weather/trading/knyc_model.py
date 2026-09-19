"""Small exported KNYC strategy forests; inference needs only the standard library."""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nice_weather.trading.us_markets import CLIMATE_ZONE

FEATURES = [
    "hour",
    "current",
    "observed_proxy_high",
    "decline",
    "since_high",
    "obs_age",
    "doy_sin",
    "doy_cos",
    "trend60",
    "trend120",
    "hrrr_remaining",
]


def features(observations, asof, hrrr):
    local = datetime.fromtimestamp(asof, ZoneInfo("America/New_York"))
    start = datetime.combine(local.date(), datetime.min.time(), CLIMATE_ZONE).timestamp()
    obs = sorted((t, temp) for t, temp in observations.items() if start <= t <= asof)
    if not obs or asof - obs[-1][0] > 5400:
        raise ValueError("STALE_KNYC_OBSERVATIONS")
    if obs[0][0] - start > 5400 or any(
        b[0] - a[0] > 5400 for a, b in zip(obs, obs[1:], strict=False)
    ):
        raise ValueError("INCOMPLETE_CLIMATE_DAY_OBSERVATIONS")
    high = max(math.floor(temp + 0.5) for _, temp in obs)
    previous = max((math.floor(temp + 0.5) for _, temp in obs[:-1]), default=high)
    current = obs[-1][1]
    peak = max(t for t, temp in obs if math.floor(temp + 0.5) == high)
    values = {
        "hour": local.hour + local.minute / 60,
        "current": current,
        "observed_proxy_high": high,
        "decline": high - current,
        "since_high": (asof - peak) / 3600,
        "obs_age": (asof - obs[-1][0]) / 60,
        "doy_sin": math.sin(2 * math.pi * local.timetuple().tm_yday / 365.2425),
        "doy_cos": math.cos(2 * math.pi * local.timetuple().tm_yday / 365.2425),
    }
    for lag in (60, 120):
        earlier = [(t, temp) for t, temp in obs if t <= asof - lag * 60]
        values[f"trend{lag}"] = (
            current - earlier[-1][1]
            if (earlier and asof - lag * 60 - earlier[-1][0] <= 5400)
            else None
        )
    end = start + timedelta(days=1).total_seconds()
    if (
        not hrrr.get("complete")
        or hrrr["received_at"] > asof
        or not 0 <= asof - hrrr["cycle"] <= 6 * 3600
    ):
        raise ValueError("STALE_OR_INCOMPLETE_HRRR")
    points = [p for p in hrrr["points"] if asof < p["valid_at"] <= end]
    if not points or max(p["valid_at"] for p in points) < end - 3600:
        raise ValueError("HRRR_DAY_END_NOT_COVERED")
    if any(p["received_at"] > asof for p in points):
        raise ValueError("FUTURE_HRRR_POINT")
    values["hrrr_remaining"] = max(p["temperature_f"] for p in points) - high
    return values, previous, obs[-1][0]


def forest_predict(forest, values):
    output = [0.0] * len(forest["classes"])
    for tree in forest["trees"]:
        index = 0
        while tree["left"][index] != -1:
            index = (
                tree["left"]
                if values[tree["feature"][index]] <= tree["threshold"][index]
                else tree["right"]
            )[index]
        row = tree["value"][index]
        total = sum(row)
        for i, value in enumerate(row):
            output[i] += value / total / len(forest["trees"])
    return output


def temper(values, temperature):
    values = [max(p, 1e-12) ** (1 / temperature) for p in values]
    total = sum(values)
    return [p / total for p in values]


class StrategyModel:
    def __init__(self, path: Path):
        self.model = json.loads(path.read_text(encoding="utf-8"))
        if self.model["features"] != FEATURES or self.model["station"] != "KNYC":
            raise ValueError("INCOMPATIBLE_STRATEGY_MODEL")

    def predict(self, weather, contracts, asof):
        model = self.model
        metar = weather["metar"]
        if metar["station"] != "KNYC" or not 0 <= asof - metar["received_at"] <= 120:
            raise ValueError("STALE_METAR_RECEIPT")
        observations, receipts = {}, {}
        for row in metar["data"]:
            if row.get("icaoId") != "KNYC":
                raise ValueError("METAR_STATION_MISMATCH")
            temp, stamp = row.get("temp"), row.get("obsTime")
            if type(temp) in (int, float) and type(stamp) in (int, float):
                if math.isfinite(temp) and -60 <= temp <= 60 and stamp <= asof:
                    observations[stamp] = temp * 1.8 + 32
                    receipts[stamp] = row.get("first_received_at")
        values, previous, observation_at = features(observations, asof, weather["hrrr"])
        x = [
            values[name] if values[name] is not None else model["medians"][i]
            for i, name in enumerate(FEATURES)
        ]
        end = forest_predict(model["end"], x)
        p_end = temper(end, model["end_temperature"])[model["end"]["classes"].index(1)]
        raw = forest_predict(model["residual"], x)
        support = model["support"]
        residual = dict(zip(model["residual"]["classes"], raw, strict=True))
        q = temper(
            [0.98 * residual.get(v, 0) + 0.02 * model["prior"][i] for i, v in enumerate(support)],
            model["temperature"],
        )
        high = values["observed_proxy_high"]
        probabilities = {
            c["yes_token_id"]: sum(
                p
                for value, p in zip(support, q, strict=True)
                if (c["lower"] is None or high + value >= c["lower"])
                and (c["upper"] is None or high + value <= c["upper"])
            )
            for c in contracts
        }
        if not model["trained_at"] <= asof or not contracts:
            raise ValueError("MODEL_CUTOFF_OR_CONTRACT_MISSING")
        if contracts[0]["settlement_source"] not in model["settlement_sources"]:
            raise ValueError("UNVALIDATED_SETTLEMENT_MODEL")
        return {
            "station": "KNYC",
            "day": contracts[0]["local_day"],
            "received_at": asof,
            "data_cutoff": asof,
            "model_trained_at": model["trained_at"],
            "model_version": model["version"],
            "settlement_source": contracts[0]["settlement_source"],
            "p_end": p_end,
            "floor": high,
            "previous_floor": previous,
            "is_high": high > previous,
            "observation_received_at": receipts.get(observation_at),
            "observation_at": observation_at,
            "probabilities": probabilities,
            "features": values,
            "capture_ids": [metar["capture_id"]],
            "hrrr_cycle": weather["hrrr"]["cycle"],
            "validation": model["validation"],
        }


def publish_predictions(store):
    """Run once per weather poll in the existing collector; never loads trading credentials."""
    import time

    path = Path(__file__).resolve().parents[3] / "config/knyc-strategy-model.json"
    snapshot = store.snapshot()
    try:
        model = StrategyModel(path)
    except (OSError, ValueError, KeyError):
        model = None
    for venue in ("kalshi", "poly_us"):
        now = time.time()
        contracts = [c for c in snapshot["contracts"] if c["venue"] == venue]
        try:
            if model is None:
                raise ValueError("KNYC_STRATEGY_MODEL_UNAVAILABLE")
            prediction = model.predict(snapshot["weather"], contracts, now)
        except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
            prediction = {
                "station": "KNYC",
                "received_at": now,
                "reason": str(exc),
                "day": contracts[0]["local_day"] if contracts else None,
                "status": "unavailable",
            }
        store.publish("prediction", venue, prediction, now)

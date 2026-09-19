"""Reuse KNYC retrospective features and HRRR; export forests without a VM ML runtime."""

import argparse
import bisect
import hashlib
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar
from sklearn.ensemble import RandomForestClassifier

from nice_weather.trading.knyc_model import FEATURES
from nice_weather.trading.us_markets import CLIMATE_ZONE


def export(forest):
    return {
        "classes": forest.classes_.astype(int).tolist(),
        "trees": [
            {
                "left": e.tree_.children_left.tolist(),
                "right": e.tree_.children_right.tolist(),
                "feature": e.tree_.feature.tolist(),
                "threshold": e.tree_.threshold.tolist(),
                "value": e.tree_.value[:, 0, :].tolist(),
            }
            for e in forest.estimators_
        ],
    }


def fit(frame, cutoff):
    cutoff = pd.Timestamp(cutoff)
    calibration = cutoff - pd.Timedelta(days=60)
    train = frame[
        (frame.day < str(calibration.date()))
        & (pd.to_datetime(frame.label_product_issued_at, utc=True) < calibration.tz_localize("UTC"))
    ]
    cal = frame[
        (frame.day >= str(calibration.date()))
        & (frame.day < str(cutoff.date()))
        & (pd.to_datetime(frame.label_product_issued_at, utc=True) < cutoff.tz_localize("UTC"))
    ]
    if train.day.nunique() < 365 or cal.day.nunique() < 40:
        raise ValueError("Insufficient training/calibration days")
    medians = train[FEATURES].median()
    x = train[FEATURES].fillna(medians).to_numpy()
    cx = cal[FEATURES].fillna(medians).to_numpy()
    weights = 1 / train.groupby("day").day.transform("size").to_numpy()
    weights *= len(weights) / weights.sum()
    support = np.arange(-20, 51)
    prior = np.bincount(train.target.to_numpy(int) + 20, weights=weights, minlength=71) + 1
    prior /= prior.sum()
    forests, temps = {}, {}
    for name, target in (("end", "hourly_ended"), ("residual", "target")):
        rf = RandomForestClassifier(
            n_estimators=64,
            max_depth=4,
            min_samples_leaf=60,
            max_features=1.0,
            n_jobs=4,
            random_state=42,
        )
        rf.fit(x, train[target].to_numpy(int), sample_weight=weights)
        p = rf.predict_proba(cx)
        y = cal[target].to_numpy(int)
        if name == "residual":
            expanded = np.zeros((len(cal), len(support)))
            expanded[:, rf.classes_.astype(int) + 20] = p
            p = 0.98 * expanded + 0.02 * prior
            y += 20
        elif list(rf.classes_) != [0, 1]:
            raise ValueError("Missing end-event class")

        def loss(temperature, p=p, y=y):
            z = np.maximum(p, 1e-12) ** (1 / temperature)
            z /= z.sum(axis=1, keepdims=True)
            return -np.log(z[np.arange(len(y)), y]).mean()

        temps[name] = float(minimize_scalar(loss, bounds=(0.5, 2), method="bounded").x)
        forests[name] = export(rf)
    return forests | {
        "features": FEATURES,
        "medians": medians.tolist(),
        "temperature": temps["residual"],
        "end_temperature": temps["end"],
        "prior": prior.tolist(),
        "support": support.tolist(),
        "train_last": train.day.max(),
        "calibration_last": cal.day.max(),
    }


def main(features_path, hrrr_path, output):
    from nice_weather.trading.knyc_model import forest_predict, temper

    frame = pd.read_csv(features_path)
    forecasts = [json.loads(line) for line in hrrr_path.open(encoding="utf-8")]
    cycles = [r["cycle"] for r in forecasts]
    remaining = []
    for row in frame.itertuples():
        i = bisect.bisect_right(cycles, row.t - 7200) - 1
        end = (
            datetime.fromisoformat(row.day).replace(tzinfo=CLIMATE_ZONE) + timedelta(days=1)
        ).timestamp()
        values = []
        if i >= 0 and row.t - cycles[i] <= 6 * 3600:
            f = forecasts[i]
            values = [
                (cycles[i] + h * 3600, v)
                for h, v in zip(f["forecast_hours"], f["temperature_f"], strict=True)
                if row.t < cycles[i] + h * 3600 <= end
            ]
        remaining.append(
            max(v for _, v in values) - row.observed_proxy_high
            if values and max(t for t, _ in values) >= end - 3600
            else np.nan
        )
    frame["hrrr_remaining"] = remaining
    frame = frame.dropna(subset=["hrrr_remaining"])
    if not frame.target.between(-20, 50).all():
        raise ValueError("Signed residual exceeds retained support")
    frozen = fit(frame, "2026-01-01")
    test = frame[frame.day >= "2026-01-01"]
    observed, predicted = [], []
    for _, row in test.iterrows():
        x = [
            float(row[name]) if pd.notna(row[name]) else frozen["medians"][i]
            for i, name in enumerate(FEATURES)
        ]
        p = temper(forest_predict(frozen["end"], x), frozen["end_temperature"])[1]
        observed.append(int(row.hourly_ended))
        predicted.append(p)
    triggered = np.array(predicted) >= 0.9
    validation = {
        "kind": "chronological_retrospective_weather_only",
        "test_days": int(test.day.nunique()),
        "test_rows": len(test),
        "test_start": test.day.min(),
        "test_end": test.day.max(),
        "end_brier": float(np.mean((np.array(predicted) - observed) ** 2)),
        "end_threshold_rows": int(triggered.sum()),
        "end_threshold_frequency": float(np.array(observed)[triggered].mean())
        if triggered.any()
        else None,
        "historical_received_at": False,
        "historical_hrrr_availability": "nominal cycle + 2h",
        "executable_backtest": False,
        "settlement_label": "archived NWS CLI, revision-limited",
    }
    cutoff = str((pd.Timestamp(frame.day.max()) + pd.Timedelta(days=2)).date())
    model = fit(frame, cutoff) | {
        "station": "KNYC",
        "trained_at": time.time(),
        "settlement_sources": ["nws_cli"],
        "validation": validation,
        "source_hashes": {
            "features": hashlib.sha256(features_path.read_bytes()).hexdigest(),
            "hrrr": hashlib.sha256(hrrr_path.read_bytes()).hexdigest(),
        },
    }
    identity = hashlib.sha256(json.dumps(model, sort_keys=True).encode()).hexdigest()[:16]
    model["version"] = "knyc-strategy-hrrr-" + identity
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(model, allow_nan=False), encoding="utf-8")
    report = output.with_suffix(".validation.json")
    report.write_text(json.dumps(validation, indent=2), encoding="utf-8")
    print(
        json.dumps(
            validation | {"model_bytes": output.stat().st_size, "model_version": model["version"]}
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("features", type=Path)
    parser.add_argument("hrrr", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    main(args.features, args.hrrr, args.output)

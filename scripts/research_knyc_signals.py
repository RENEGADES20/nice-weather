"""Local chronological KNYC signal diagnosis; no network, account or VM writes."""

import argparse
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

from nice_weather.trading.knyc_model import FEATURES, StrategyModel
from nice_weather.trading.signal_research import research_signals, summarize


def archived_records(features, hrrr, start, end, feed=None):
    from train_knyc_strategy import fit, load_features

    frame = load_features(features, hrrr)
    model = StrategyModel.__new__(StrategyModel)
    model.model = fit(frame, start) | {
        "station": "KNYC", "trained_at": time.time(), "settlement_sources": ["nws_cli"],
        "version": "knyc-research-frozen-" + start,
        "validation": {"production_validated": False, "historical_received_at": False,
                       "hrrr_availability_assumption": "nominal cycle + 2h"},
    }
    metadata = {k: v for k, v in model.model.items() if k not in {
        "end", "residual", "prior", "support", "medians", "features"
    }}
    historical_contracts = []
    if feed:
        with sqlite3.connect(feed.resolve().as_uri() + "?mode=ro", uri=True) as con:
            historical_contracts = [(stamp, venue, json.loads(body)) for stamp, venue, body in
                                    con.execute("SELECT received,key,body FROM feed_events "
                                                "WHERE kind='contracts' ORDER BY received,seq")]

    def records():
        for _, row in frame[(frame.day >= start) & (frame.day <= end)].sort_values("t").iterrows():
            values = {name: float(row[name]) if math.isfinite(row[name]) else None
                      for name in FEATURES}
            # No future contract snapshot or final settlement label is fed into selection.
            known = {}
            for stamp, venue, contracts in historical_contracts:
                if stamp > row.t:
                    break
                known[venue] = [c for c in contracts if c["local_day"] == row.day]
            groups = list(known.values()) or [[]]
            for contracts in groups:
                # A weather-only target defines the model's physical quantity, not a market bin.
                target = contracts or [{
                    "local_day": row.day, "settlement_source": "nws_cli",
                    "yes_token_id": "weather_only", "lower": None, "upper": None,
                }]
                try:
                    weather = model.predict_features(values, target, row.t,
                                                     context="historical_source")
                except ValueError as exc:
                    yield {"asof": row.t, "contracts": contracts,
                           "weather": {"day": row.day, "status": "unavailable", "reason": str(exc)}}
                    continue
                weather.update(
                    source_time=float(max(row.latest_observation_at, row.hrrr_available_at)),
                    data_cutoff=float(max(row.latest_observation_at, row.hrrr_available_at)),
                    observation_at=float(row.latest_observation_at), observation_received_at=None,
                    previous_floor=float(row.previous_proxy_high),
                    is_high=row.observed_proxy_high > row.previous_proxy_high,
                    capture_ids=["features:" + str(row.name)],
                )
                if not contracts:
                    weather["probabilities"] = {}
                yield {"asof": float(row.t), "contracts": contracts, "weather": weather}
    return records(), metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, help="Normalized chronological research JSONL")
    parser.add_argument("--features", type=Path)
    parser.add_argument("--hrrr", type=Path)
    parser.add_argument("--feed", type=Path)
    parser.add_argument("--start", default="2026-09-01")
    parser.add_argument("--end", default="2026-09-13")
    parser.add_argument("--context", choices=["historical_source", "historical_received"],
                        default="historical_source")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.inputs:
        records = [json.loads(line) for line in args.inputs.read_text().splitlines()
                   if line.strip()]
        metadata = {}
    else:
        if not args.features or not args.hrrr or args.context != "historical_source":
            parser.error("Archive features require --features, --hrrr and historical_source")
        records, metadata = archived_records(
            args.features, args.hrrr, args.start, args.end, args.feed)
    result = {"context": args.context, "start": args.start, "end": args.end,
              "execution": "signal_only", "model": metadata,
              "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (args.inputs, args.features, args.hrrr) if p},
              "results": summarize(research_signals(records, context=args.context))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2),
                           encoding="utf-8")
    print(json.dumps({key: value["counts"] for key, value in result["results"].items()}))


if __name__ == "__main__":
    main()

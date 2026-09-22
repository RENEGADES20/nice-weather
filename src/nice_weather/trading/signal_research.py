"""Historical signals only; task 3 owns fills and account accounting."""

from collections import Counter

from nice_weather.trading.signals import STRATEGY_IDS
from nice_weather.trading.signals_v2 import evaluate


def research_signals(records, *, context="historical_source"):
    if context not in {"historical_source", "historical_received"}:
        raise ValueError("RESEARCH_CONTEXT_REQUIRED")
    previous = float("-inf")
    for row in records:
        asof = row["asof"]
        if asof < previous:
            raise ValueError("RESEARCH_EVENTS_OUT_OF_ORDER")
        previous = asof
        for strategy in row.get("strategies", STRATEGY_IDS):
            signal = evaluate(strategy, row.get("contracts", []), row["weather"],
                              row.get("books", {}), asof, row.get("risk"), context=context)
            yield signal


def summarize(signals):
    """Counts are evaluations, never inferred fills or independent opportunities."""
    groups = {}
    for signal in signals:
        key = f"{signal['venue'] or 'weather_only'}:{signal['strategy']}"
        group = groups.setdefault(key, {"counts": Counter(), "reasons": Counter(), "examples": {},
                                        "days": set(), "first_asof": signal["asof"],
                                        "last_asof": signal["asof"]})
        if signal["day"] is not None:
            group["days"].add(signal["day"])
        group["last_asof"] = signal["asof"]
        counts = group["counts"]
        counts["evaluations"] += 1
        counts["input_available"] += bool(signal.get("input_available"))
        counts["in_window"] += bool(signal.get("in_window"))
        counts["weather_trigger"] += bool(signal["triggered"])
        counts["candidate"] += signal["action"] == "buy"
        # Execution outcomes exist only when provided by the native execution chain.
        counts["execution_accepted"] += any(
            e.get("status") in {"ACCEPTED", "PARTIALLY_FILLED", "FILLED"}
            for e in signal.get("executions", [])
        )
        counts["fill"] += any(e.get("filled", 0) > 0 for e in signal.get("executions", []))
        group["reasons"][signal["reason"]] += 1
        example_key = f"{signal['triggered']}:{signal['reason']}"
        group["examples"].setdefault(example_key, signal)
    for group in groups.values():
        group["days"] = sorted(group["days"])
    return groups

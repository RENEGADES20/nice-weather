"""Read-only statistics on native equity samples; never manufactures execution history."""

from __future__ import annotations

import math

import pandas as pd


def daily_statistics(samples: list[dict]) -> dict:
    if not samples:
        return {"days": [], "sharpe": None, "complete_days": 0}
    frame = pd.DataFrame(samples)
    frame["at"] = pd.to_datetime(frame.ts, unit="ns", utc=True).dt.tz_convert("America/New_York")
    frame["day"] = frame["at"].dt.date
    frame["gap"] = frame["at"].diff().dt.total_seconds()
    # Onboarding and the currently open NY day are partial; retain but exclude from Sharpe.
    days = frame.groupby("day").agg(
        open=("equity", "first"),
        close=("equity", "last"),
        known=("equity", "count"),
        samples=("equity", "size"),
        first=("at", "first"),
        last=("at", "last"),
        maximum_gap=("gap", "max"),
    )
    days["complete"] = (
        (days.known == days.samples)
        & (days.index > frame.day.iloc[0])
        & (days.index < frame.day.iloc[-1])
    )
    days["pnl"] = days.close - days.close.shift(1)
    days["return"] = days.close / days.close.shift(1) - 1
    consecutive = pd.Series(pd.to_datetime(days.index), index=days.index).diff().dt.days == 1
    days["complete"] &= consecutive
    midnight = pd.to_datetime(days.index).tz_localize("America/New_York")
    next_midnight = midnight + pd.DateOffset(days=1)
    # Require observations near both boundaries and no unobserved interval > 5 minutes.
    # A single daily point cannot establish a complete monitored market day.
    days["complete"] &= (
        ((days["first"].array - midnight).total_seconds() <= 300)
        & ((next_midnight - days["last"].array).total_seconds() <= 300)
        & (days["maximum_gap"] <= 300)
        & days["close"].shift(1).notna()
    )
    returns = days.loc[days.complete, "return"].dropna()
    sharpe = None
    if len(returns) >= 30 and returns.std(ddof=1) > 0:
        sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(365))
    days.index = days.index.astype(str)
    days = days.drop(columns=["first", "last"])
    return {
        "days": days.reset_index().replace({float("nan"): None}).to_dict("records"),
        "sharpe": sharpe,
        "complete_days": int(days.complete.sum()),
    }

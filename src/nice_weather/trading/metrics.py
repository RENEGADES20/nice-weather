"""Read-only statistics on native equity samples; never manufactures execution history."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd


def pnl_view(samples, cash, fills, started_ns):
    """One NY-day definition for both curve and calendar; missing days stay missing."""
    zone = ZoneInfo("America/New_York")
    grouped = {}
    points = []
    for row in sorted(samples, key=lambda r: r["ts"]):
        at = datetime.fromtimestamp(row["ts"] / 1e9, UTC)
        day = at.astimezone(zone).date()
        value = row["equity"] - cash if row["equity"] is not None else None
        if points and int(at.timestamp()) - points[-1]["time"] > 300:
            points.append({"time": points[-1]["time"] + 1, "value": None})
        points.append({"time": int(at.timestamp()), "value": value})
        grouped.setdefault(day, []).append(row)
    counts, fees = {}, {}
    for fill in fills:
        day = datetime.fromtimestamp(fill["ts"] / 1e9, UTC).astimezone(zone).date()
        counts[day] = counts.get(day, 0) + 1
        fees[day] = fees.get(day, 0) + fill["fee"]
    start = datetime.fromtimestamp(started_ns / 1e9, UTC).astimezone(zone).date()
    today = datetime.now(zone).date()
    days, previous_day, previous_close, previous_realized = [], None, None, None
    for day, rows in sorted(grouped.items()):
        last = rows[-1]
        close = last["equity"]
        baseline = (
            cash
            if day == start
            else previous_close
            if previous_day == day - timedelta(days=1)
            else None
        )
        # A day needs a valid close; historical end gaps cannot be shifted into the next day.
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), zone).timestamp()
        complete_close = day == today or end - last["ts"] / 1e9 <= 300
        valid = close is not None and baseline is not None and complete_close
        changes = [b["ts"] - a["ts"] for a, b in zip(rows, rows[1:], strict=False)]
        incomplete = any(r["equity"] is None for r in rows) or any(
            g > 300_000_000_000 for g in changes
        )
        realized_base = 0 if day == start else previous_realized if baseline is not None else None
        realized_change = (
            last["realized"] - realized_base
            if realized_base is not None and last.get("realized") is not None
            else None
        )
        days.append(
            {
                "day": day.isoformat(),
                "pnl": close - baseline if valid else None,
                "fills": counts.get(day, 0),
                "fees": fees.get(day, 0),
                "status": "In progress"
                if day == today
                else "Data gap"
                if not valid or incomplete
                else "Complete",
                "realized_change": realized_change,
                "unrealized_change": close - baseline - realized_change
                if valid and realized_change is not None
                else None,
            }
        )
        previous_day, previous_close = day, close if complete_close else None
        previous_realized = last.get("realized") if complete_close else None
    return {"points": points, "days": days}


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

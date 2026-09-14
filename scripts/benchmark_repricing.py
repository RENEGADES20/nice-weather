"""Read-only comparison on one database: baseline Git ref versus current source."""

from __future__ import annotations

import inspect
import json
import statistics
import subprocess
import sys
import types
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]


def source_module(name, path, revision):
    if revision and Path(revision).is_dir():
        source = (Path(revision) / path).read_text(encoding="utf-8")
    else:
        source = (
            subprocess.check_output(["git", "show", f"{revision}:{path}"], cwd=ROOT).decode()
            if revision
            else (ROOT / path).read_text(encoding="utf-8")
        )
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / path)
    sys.modules[name] = module
    exec(compile(source, str(ROOT / path), "exec"), module.__dict__)
    return module


def run(database, revision, market_day=None):
    query = source_module("benchmark_query", "src/nice_weather/queries.py", revision)
    dashboard = source_module("benchmark_dashboard", "src/nice_weather/dashboard.py", revision)
    dashboard.st = types.SimpleNamespace(session_state={})
    frozen = datetime.now(UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz or UTC)

    dashboard.datetime = Clock
    calls = []

    class TimedQuery(query.DashboardQuery):
        def _query(self, sql, parameters=()):
            start = perf_counter()
            result = super()._query(sql, parameters)
            calls.append((perf_counter() - start) * 1000)
            return result

    dashboard.DashboardQuery = TimedQuery
    q = TimedQuery(database)
    market = q._query(
        "SELECT c.event_id,c.local_day,c.timezone,b.bin_id FROM contract_versions c "
        "JOIN contract_bins b USING(contract_version_id) "
        + ("WHERE c.local_day=? " if market_day else "")
        + "ORDER BY c.local_day,abs(b.ordinal-4) LIMIT 2",
        (market_day,) if market_day else (),
    )
    samples = {}
    for case in ("cold", "warm", "other-bin", "warm"):
        for _ in range(5):
            if case == "cold":
                dashboard.st.session_state.clear()
            calls.clear()
            row = market[-1] if case == "other-bin" else market[0]
            start = perf_counter()
            series, inputs, *_ = dashboard._timeline_data(
                Path(database),
                row["event_id"],
                date.fromisoformat(row["local_day"]),
                row["timezone"],
                1,
                row["bin_id"],
                ["forecast", "metar", "weather-gov"],
            )
            for key, data in (("series", series), ("inputs", inputs)):
                state = dashboard.st.session_state
                args = (data, state.get("bench_" + key, {}))
                if "previous_points" in inspect.signature(dashboard._series_delta).parameters:
                    args += (state.get("refs_" + key),)
                _, state["bench_" + key] = dashboard._series_delta(*args)
                state["refs_" + key] = {item["id"]: item["points"] for item in data}
            elapsed = (perf_counter() - start) * 1000
            samples.setdefault(case, []).append(
                {
                    "total": elapsed,
                    "sql": sum(calls),
                    "prepare": elapsed - sum(calls),
                    "queries": len(calls),
                }
            )
    return {
        case: {
            key: {
                "median": statistics.median(r[key] for r in rows),
                "max": max(r[key] for r in rows),
            }
            for key in rows[0]
        }
        for case, rows in samples.items()
    }


if __name__ == "__main__":
    day = sys.argv[4] if len(sys.argv) > 4 else None
    print(
        json.dumps(
            {
                "baseline": run(sys.argv[1], sys.argv[2], day),
                "current": run(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 else None, day),
            },
            indent=2,
        )
    )

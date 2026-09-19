"""Bounded read-only runtime samples. Final settlement/market-day acceptance is separate."""

import argparse
import json
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

UNITS = ["nice-weather-terminal", "nice-weather-knyc-feed", "nice-weather-knyc-hrrr",
         "nice-weather-knyc-paper@kalshi", "nice-weather-knyc-paper@poly_us",
         "nice-weather-knyc-backtest"]


def query(path, sql):
    deadline = time.monotonic() + .25
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=.2)) as con:
        con.row_factory = sqlite3.Row
        con.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
        return [dict(row) for row in con.execute(sql)]


def sample(root, runtime):
    now = time.time()
    result = {"time": now, "monotonic": time.monotonic(),
              "free_bytes": shutil.disk_usage(root).free,
              "sha": json.loads((runtime / "runtime-manifest.json").read_text())["commit"]}
    result["units"] = {}
    for unit in UNITS:
        raw = subprocess.check_output(
            ["systemctl", "show", unit, "--property=MainPID,ActiveState,NRestarts,MemoryCurrent,"
             "CPUUsageNSec,Result"], text=True, timeout=5)
        state = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        pid = int(state["MainPID"])
        if pid:
            try:
                state["io"] = dict(line.split(": ", 1) for line in
                                   Path(f"/proc/{pid}/io").read_text().splitlines())
            except OSError as exc:
                state["io_error"] = type(exc).__name__
        result["units"][unit] = state
    result["files"] = {path.name: path.stat().st_size for name in ("feed", "results")
                       for suffix in (".sqlite3", ".sqlite3-wal")
                       if (path := root / (name + suffix)).is_file()}
    result["latest"] = query(root / "feed.sqlite3",
                             "SELECT kind,key,seq,received FROM feed_latest")
    accounts = query(root / "results.sqlite3",
                     "SELECT account,status,updated,config FROM runs WHERE mode='sandbox'")
    result["accounts"] = []
    for row in accounts:
        cursor = int(json.loads(row.pop("config")).get("feed_cursor", 0))
        pending = query(root / "feed.sqlite3", "SELECT seq,received FROM feed_events WHERE seq>"
                        + str(cursor) + " ORDER BY seq LIMIT 1")
        result["accounts"].append(row | {"cursor": cursor,
                                        "oldest_pending_age": max(0, now - pending[0]["received"])
                                        if pending else 0})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--root", type=Path, default=Path("/var/lib/nice-weather-knyc"))
    parser.add_argument("--runtime", type=Path, default=Path("/opt/nice-weather/knyc-current"))
    parser.add_argument("--samples", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.samples <= 2161:
        parser.error("At most 36 hours of one-minute samples per run")
    # Exclusive create prevents two observers from interleaving one acceptance record.
    with args.output.open("x", encoding="utf-8") as output:
        for index in range(args.samples):
            started = time.monotonic()
            try:
                result = sample(args.root, args.runtime)
            except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
                result = {"time": time.time(), "error": type(exc).__name__}
            result["sampling_seconds"] = time.monotonic() - started
            output.write(json.dumps(result) + "\n")
            output.flush()
            if index + 1 < args.samples:
                time.sleep(max(0, 60 - (time.monotonic() - started)))


if __name__ == "__main__":
    main()

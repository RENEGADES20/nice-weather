"""Bounded, asynchronous chart cache. Existing ticks stay the only durable source."""

import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import RLock

from nice_weather.trading.storage import connect


def read_history(path, token, cursor=0):
    with connect(path, readonly=True) as con:
        con.execute("BEGIN")
        end = con.execute("SELECT COALESCE(MAX(rowid),0) FROM market_top_ticks").fetchone()[0]
        # Initial aggregation runs off the interaction thread. Subsequent reads scan
        # only the new global rowid interval, without rescanning a token's history.
        index = " NOT INDEXED" if cursor else ""
        rows = [
            dict(r)
            for r in con.execute(
                "SELECT MAX(rowid) sample,received_at,mid FROM market_top_ticks"
                + index
                + " WHERE rowid>? AND rowid<=? AND token_id=? AND source='clob_ws' "
                "AND event_kind IN ('quote','snapshot') "
                "GROUP BY substr(received_at,1,16) ORDER BY received_at",
                (cursor, end, token),
            )
        ]
    return end, rows


class PriceHistory:
    def __init__(self):
        self.entries = OrderedDict()
        self.lock = RLock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="chart-history")

    def get(self, path, token):
        key = (str(path), token)
        with self.lock:
            entry = self.entries.setdefault(
                key,
                dict(cursor=0, points={}, future=None, due=0, ready=False, error=None, revision=0),
            )
            self.entries.move_to_end(key)
            future = entry["future"]
            if future and future.done():
                try:
                    entry["cursor"], rows = future.result()
                    if rows:
                        entry["points"].update((r["received_at"][:16], r) for r in rows)
                        entry["revision"] += 1
                    entry.update(ready=True, error=None)
                except Exception as exc:
                    entry["error"] = type(exc).__name__
                entry.update(future=None, due=time.monotonic() + 2)
            if entry["future"] is None and time.monotonic() >= entry["due"]:
                entry["future"] = self.pool.submit(read_history, path, token, entry["cursor"])
            while len(self.entries) > 32:
                _, old = self.entries.popitem(last=False)
                if old["future"]:
                    old["future"].cancel()
            return (
                list(entry["points"].values()),
                entry["revision"],
                entry["ready"],
                entry["error"],
            )

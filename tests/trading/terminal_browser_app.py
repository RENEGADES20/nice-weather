"""Local/CI acceptance app: real terminal, request queue and native Paper worker.

Public transport alone is replaced by deterministic depth. Never deployed to the VM.
"""

import json
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import streamlit as st

from nice_weather.trading import depth
from nice_weather.trading.history import read_history
from nice_weather.trading.recovery import PaperRunner
from nice_weather.trading.storage import Results, connect
from nice_weather.trading.terminal import terminal
from nice_weather.trading.worker import run_config, sandbox_worker

ROOT = Path(os.environ["NICE_WEATHER_TERMINAL_TEST_ROOT"])
DB = ROOT / "history.sqlite3"
os.environ["NICE_WEATHER_DEPTH_PORT"] = "8768"


@st.cache_resource
def start():
    from nice_weather.trading import history

    def slow_history(*args):
        if not args[2]:
            time.sleep(3)  # Prove cold chart I/O cannot block market selection or orders.
        return read_history(*args)

    history.read_history = slow_history

    class FixtureFeed(depth.DepthFeed):
        def run(self):
            while not self.closed.wait(0.2):
                for token in self.wanted():
                    self.ingest(
                        {
                            "event_type": "book",
                            "asset_id": token,
                            "timestamp": str(int(time.time() * 1000)),
                            "bids": [{"price": ".39", "size": "50"}],
                            "asks": [{"price": ".40", "size": "2"}, {"price": ".41", "size": "30"}],
                        }
                    )

    depth.DepthFeed = FixtureFeed

    def work():
        root = Results(ROOT / "results.sqlite3")
        if not root.run(account="sandbox-browser"):
            config = run_config("sandbox-browser", "sandbox") | {"started_ns": time.time_ns()}
            root.create("browser", "sandbox-browser", "sandbox", config)
            runner = PaperRunner(root, root.run("browser"))
            now = datetime.now(UTC)
            for i in range(4):
                row = dict(
                    yes_token_id=str(i * 2 + 1),
                    no_token_id=str(i * 2 + 2),
                    condition_id=f"condition-{i}",
                    tick_size=0.01,
                    label=f"{80 + i} F",
                    local_day=now.date().isoformat(),
                    station_id="KLGA",
                    timezone="America/New_York",
                    parse_status="parsed",
                    ambiguities_json="[]",
                    closed=False,
                    active=True,
                    accepting_orders=True,
                    observation_end=(now + timedelta(days=2)).isoformat(),
                    minimum_order_size=5,
                    fee_rate=0.02,
                    fee_exponent=1,
                    fee_known=True,
                )
                runner.apply(
                    f"contract-{i}", {"kind": "contract", "ts": time.time_ns(), "data": row}
                )
            runner.session.dispose()
        sandbox_worker(ROOT, ROOT / "absent-source", "sandbox-browser")

    with connect(DB) as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS market_top_ticks "
            "(token_id,source,event_kind,received_at,mid)"
        )
        if not con.execute("SELECT 1 FROM market_top_ticks LIMIT 1").fetchone():
            now = datetime.now(UTC)
            con.executemany(
                "INSERT INTO market_top_ticks VALUES (?,'clob_ws','quote',?,?)",
                [
                    (str(t), (now - timedelta(seconds=10000 - i)).isoformat(), 0.4 + i % 3 * 0.01)
                    for t in range(1, 9)
                    for i in range(10000)
                ],
            )
    threading.Thread(target=work, daemon=True).start()
    return True


start()
st.set_page_config(layout="wide")
mode = st.radio("Mode", ["Paper", "Live"], horizontal=True)
terminal(ROOT, DB, mode, "sandbox-browser")
if st.button("Read acceptance receipt"):
    with connect(ROOT / "results.sqlite3", readonly=True) as con:
        row = con.execute("SELECT snapshot FROM runs WHERE account='sandbox-browser'").fetchone()
        st.json(json.loads(row[0]))

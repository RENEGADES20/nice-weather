"""Real authenticated API/SQLite queue/native worker with local MockTransport only."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from us_live_fixture import Exchange

from nice_weather.trading.api import create_app
from nice_weather.trading.us_live import LiveWorker


def app(root):
    api = create_app(root, password="local-fixture-only", origin="http://localhost:5176")

    @asynccontextmanager
    async def lifespan(_):
        exchange = Exchange(root)
        worker = LiveWorker(root, exchange.transport, [exchange.contract])
        worker.stream_connected = True
        worker.engine.start()

        async def run():
            while True:
                await worker.refresh()
                await worker.tick()
                await asyncio.sleep(0.2)

        task = asyncio.create_task(run())
        try:
            yield
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            worker.engine.stop()
            await exchange.transport.close()

    api.router.lifespan_context = lifespan
    return api


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    uvicorn.run(app(args.root), host="127.0.0.1", port=8775)

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
from us_live_fixture import Exchange

from nice_weather.trading.us_live import LiveWorker, collected_contracts
from nice_weather.trading.us_live_state import configure, controls


def test_live_registers_discovered_future_market_without_changing_rules(tmp_path):
    from nice_weather.trading.feed import FeedStore

    store = FeedStore(tmp_path / "feed.sqlite3")
    contract = {"condition_id": "future", "parse_status": "ambiguous", "active": True}
    store.publish("market_directory", "kalshi:2026-09-23", [contract])
    store.publish("market_directory", "poly_us:2026-09-23", [{"condition_id": "other"}])
    rows = collected_contracts(tmp_path, "kalshi", 4)
    assert set(rows) == {"future"}
    assert rows["future"]["parse_status"] == "ambiguous"
    assert rows["future"]["balance_precision"] == "0.0001"


def exercise_native_roundtrip(tmp_path, venue="poly_us", outcome="YES"):
    async def exercise():
        exchange = Exchange(tmp_path, venue)
        worker = LiveWorker(tmp_path, exchange.transport, [exchange.contract])
        worker.stream_connected = True
        worker.engine.start()
        try:
            await worker.refresh()
            assert worker.snapshot["reconciled"], worker.snapshot
            configure(
                worker.results.path,
                worker.account,
                "config",
                {
                    "revision": 0,
                    "binding": worker.binding,
                    "manual_enabled": True,
                    "whitelist": ["KNYC-TEST"],
                },
                worker.binding,
            )

            def request(key, kind, payload):
                return {"request_id": key, "kind": kind, "payload": json.dumps(payload)}

            buy = request(
                "buy",
                "order",
                {
                    "market": "KNYC-TEST",
                    "side": "BUY",
                    "outcome": outcome,
                    "quantity": "2",
                    "price": ".40",
                    "tif": "GTC",
                },
            )
            assert await worker.command(buy) == "accepted"
            assert worker.snapshot["reconciled"], worker.snapshot
            assert worker.snapshot["positions"][0]["net"] == (
                "1.0" if outcome == "YES" else "-1.0"
            )
            assert [r["trade_id"] for r in worker.snapshot["fills"]] == ["trade-remote-1"]
            from nautilus_trader.model.identifiers import ClientOrderId

            assert [str(t) for t in worker.cache.order(ClientOrderId("buy")).trade_ids] == [
                "trade-remote-1"
            ]
            assert exchange.writes == 1
            await worker.refresh()
            assert len(worker.snapshot["orders"]) == 1
            assert len(worker.snapshot["fills"]) == 1
            assert await worker.command(buy) == "accepted"
            assert exchange.writes == 1
            await worker.command(request("cancel", "cancel", {"order_id": "buy"}))
            assert worker.snapshot["orders"][0]["terminal"]
            spent = worker.snapshot["budget"]["spent"]
            sell = request(
                "sell",
                "order",
                {
                    "market": "KNYC-TEST",
                    "side": "SELL",
                    "outcome": outcome,
                    "quantity": "1",
                    "price": ".50",
                    "tif": "IOC",
                },
            )
            assert await worker.command(sell) == "accepted"
            assert worker.snapshot["reconciled"], worker.snapshot
            assert not worker.snapshot["positions"]
            from decimal import Decimal

            assert Decimal(worker.snapshot["budget"]["spent"]) > Decimal(spent)
            recovered = LiveWorker(tmp_path, exchange.transport, [exchange.contract])
            recovered.stream_connected = True
            await recovered.refresh()
            assert recovered.snapshot["reconciled"], recovered.snapshot
            assert not recovered.snapshot["positions"]
            assert recovered.snapshot["budget"] == worker.snapshot["budget"]
            assert controls(worker.results.path, worker.account)["manual_enabled"]
            assert exchange.writes == 2
        finally:
            worker.engine.stop()
            await exchange.transport.close()
            await asyncio.sleep(0.05)

    asyncio.run(exercise())

    async def stop_exercise():
        from nice_weather.trading.us_live_state import defaults

        exchange = Exchange(tmp_path / "stop", venue)
        worker = LiveWorker(tmp_path / "stop", exchange.transport, [exchange.contract])
        worker.stream_connected = True
        worker.engine.start()
        try:
            await worker.refresh()
            configure(
                worker.results.path,
                worker.account,
                "enable",
                defaults()
                | {
                    "binding": worker.binding,
                    "manual_enabled": True,
                    "whitelist": ["KNYC-TEST"],
                    "strategies": {"S1": True, "S2": False, "S3": False},
                },
                worker.binding,
            )
            for owner in ("manual", "S1"):
                await worker.command(
                    {
                        "request_id": owner,
                        "kind": "order",
                        "payload": json.dumps(
                            {
                                "market": "KNYC-TEST",
                                "owner": owner,
                                "side": "BUY",
                                "outcome": "YES",
                                "price": ".40",
                                "quantity": "2",
                                "tif": "GTC",
                            }
                        ),
                    }
                )
            worker.snapshot["authenticated"] = False
            with pytest.raises(ValueError, match="ACCOUNT_NOT_CONNECTED"):
                await worker.command(
                    {
                        "request_id": "stop",
                        "kind": "stop",
                        "payload": json.dumps({"strategies": ["S1"], "revision": 1}),
                    }
                )
            assert not controls(worker.results.path, worker.account)["strategies"]["S1"]
            await worker.refresh()
            await worker.tick()
            states = {o["owner"]: o["status"] for o in worker.snapshot["orders"]}
            assert states == {"manual": "PARTIALLY_FILLED", "S1": "CANCELED"}
            assert exchange.writes == 2
            with pytest.raises(ValueError, match="INSUFFICIENT_POSITION"):
                worker.gate(
                    worker.account,
                    exchange.contract,
                    {
                        "owner": "manual",
                        "side": "SELL",
                        "outcome": "NO",
                        "price": ".40",
                        "quantity": "1",
                        "tif": "IOC",
                    },
                )
        finally:
            worker.engine.stop()
            await exchange.transport.close()
            await asyncio.sleep(0.05)

    asyncio.run(stop_exercise())


@pytest.mark.parametrize("venue", ["poly_us", "kalshi"])
@pytest.mark.parametrize("outcome", ["YES", "NO"])
def test_native_manual_roundtrip_and_restart(tmp_path, venue, outcome):
    code = (
        "import runpy,sys; from pathlib import Path; "
        "sys.path.insert(0,str(Path(sys.argv[1]).parent)); "
        "runpy.run_path(sys.argv[1])['exercise_native_roundtrip']"
        "(Path(sys.argv[2]),sys.argv[3],sys.argv[4])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(Path(__file__).resolve()), str(tmp_path), venue, outcome],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

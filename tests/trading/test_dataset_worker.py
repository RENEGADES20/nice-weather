import json
from datetime import UTC, datetime, timedelta

import pytest

from nice_weather.adapters.fixture import load_fixture
from nice_weather.config import load_city_config
from nice_weather.contract import parse_gamma_contract
from nice_weather.market_stream import MarketStreamCollector, TokenMetadata
from nice_weather.trading.dataset import coverage, export_dataset, load_dataset
from nice_weather.trading.storage import Requests, Results, connect
from nice_weather.trading.worker import backtest_worker


def test_self_collected_export_worker_compare_cancel_and_no_leak(tmp_path, fixture_manifest):
    root = tmp_path / "trading"
    database = tmp_path / "weather.sqlite3"
    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    collector = MarketStreamCollector(config, str(database))
    collector._storage().save_discovered_contract(contract, bundle.gamma_snapshot)
    b = contract.bins[0]
    start = datetime(2026, 8, 24, 12, tzinfo=UTC)
    for index in range(4):
        for token in (b.yes_token_id, b.no_token_id):
            collector._save(
                TokenMetadata(
                    contract.event_id, b.condition_id, b.market_id, b.bin_id, token, b.label
                ),
                exchange_event_at=start + timedelta(seconds=index),
                received_at=start + timedelta(seconds=index),
                source="clob_ws",
                status="available",
                changes={
                    "best_bid": round(0.39 + index * 0.01, 2),
                    "best_ask": round(0.4 + index * 0.01, 2),
                    "bid_size": 20,
                    "ask_size": 20,
                },
                raw_event={"index": index, "token": token},
                event_kind="snapshot",
            )
    collector.close()
    path = export_dataset(
        database, root / "datasets", start.isoformat(), (start + timedelta(seconds=10)).isoformat()
    )
    payload = load_dataset(path)
    assert len(payload["coverage"]["quote_counts"]) == 2
    assert not coverage(payload, [b.yes_token_id], require_both=True)["rejection_reasons"]
    no_history = payload | {
        "events": [e for e in payload["events"] if e["data"].get("token_id") != b.no_token_id]
    }
    assert coverage(no_history, [b.yes_token_id], require_both=True)["rejection_reasons"]
    with connect(database) as con:
        con.execute("UPDATE contract_bins SET fee_rate=99")
    assert (
        export_dataset(
            database,
            root / "datasets",
            start.isoformat(),
            (start + timedelta(seconds=10)).isoformat(),
        )
        == path
    )
    requests = Requests(root / "requests" / "requests.sqlite3")
    settings = {
        "dataset_id": payload["dataset_id"],
        "tokens": [b.yes_token_id],
        "strategy_id": "acceptance_roundtrip",
        "parameters": {"quantity": 5},
        "cash": 100,
    }
    for run_id in ("one", "two", "cancelled"):
        requests.submit(run_id, "backtest-001", "backtest", "backtest", settings, ttl=86400)
    requests.submit("cancel", "backtest-001", "backtest", "cancel_run", {"run_id": "cancelled"})
    backtest_worker(root, once=True)
    results = Results(root / "results.sqlite3")
    a, b_run = results.run("one"), results.run("two")
    assert a["status"] == b_run["status"] == "completed"
    assert a["snapshot"] == b_run["snapshot"]
    snapshot = json.loads(a["snapshot"])
    assert len(snapshot["fills"]) == 2
    assert snapshot["positions"] == []
    assert results.run("cancelled")["status"] == "cancelled"
    changed = json.loads(path.read_text())
    changed["events"][-1]["ts"] += 1
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_dataset(path)

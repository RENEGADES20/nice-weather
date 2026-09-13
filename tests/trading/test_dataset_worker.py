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


def test_idle_worker_keeps_results_wal_available_for_readonly_dashboard(tmp_path, monkeypatch):
    def check_idle(_seconds):
        assert (tmp_path / "results.sqlite3-wal").exists()
        assert (tmp_path / "results.sqlite3-shm").exists()
        with connect(tmp_path / "results.sqlite3", readonly=True) as con:
            assert con.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        raise InterruptedError("End idle-worker probe")

    monkeypatch.setattr("nice_weather.trading.worker.time.sleep", check_idle)
    with pytest.raises(InterruptedError, match="End idle-worker probe"):
        backtest_worker(tmp_path)


def test_sandbox_skips_old_tick_backlog_and_loads_latest_contract(
    tmp_path, fixture_manifest, monkeypatch
):
    from nice_weather.trading.dataset import contracts
    from nice_weather.trading.worker import sandbox_worker

    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    database = tmp_path / "source.sqlite3"
    root = tmp_path / "trading"
    collector = MarketStreamCollector(config, str(database))
    collector._storage().save_discovered_contract(contract, bundle.gamma_snapshot)
    now = datetime.now(UTC)
    monkeypatch.setattr(
        "nice_weather.trading.worker.time.time_ns", lambda: int(now.timestamp() * 1e9)
    )
    item = contract.bins[0]
    for i in range(101):
        received = now - timedelta(hours=1) if i < 100 else now + timedelta(seconds=1)
        collector._save(
            TokenMetadata(
                contract.event_id,
                item.condition_id,
                item.market_id,
                item.bin_id,
                item.yes_token_id,
                item.label,
            ),
            exchange_event_at=received,
            received_at=received,
            source="clob_ws",
            status="available",
            changes={"best_bid": 0.39, "best_ask": 0.4, "bid_size": 20, "ask_size": 20},
            raw_event={"index": i},
            event_kind="snapshot",
        )
    collector.close()
    with connect(database) as con:
        con.execute(
            """INSERT INTO market_captures
            SELECT capture_id||'-revision',source,kind,event_id,market_id,requested_at,?,
                   content_hash||'-revision',payload_json FROM market_captures""",
            ((now - timedelta(minutes=1)).isoformat(),),
        )
        current = contracts(con, now.isoformat(), latest_only=True)
        assert len(current) == len(contract.bins)
        assert all(r["source_id"].endswith("-revision") for r in current)
        old = contracts(con, bundle.gamma_snapshot.received_at.isoformat(), latest_only=True)
        assert all(not r["source_id"].endswith("-revision") for r in old)
    sandbox_worker(root, database, once=True)
    with connect(root / "results.sqlite3", readonly=True) as con:
        inputs = [json.loads(r[0]) for r in con.execute("SELECT body FROM inputs")]
        saved = json.loads(con.execute("SELECT body FROM paper_state").fetchone()[0])
    assert inputs == []
    assert len(saved["metadata"]) == len(contract.bins) * 2
    assert '"bids"' not in json.dumps(saved) and '"asks"' not in json.dumps(saved)


def test_live_contract_cursor_only_reads_new_immutable_captures(tmp_path, fixture_manifest):
    from nice_weather.trading.dataset import live_contracts

    config = load_city_config()
    bundle = load_fixture(fixture_manifest, config)
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    database = tmp_path / "source.sqlite3"
    collector = MarketStreamCollector(config, str(database))
    collector._storage().save_discovered_contract(contract, bundle.gamma_snapshot)
    collector.close()
    with connect(database) as con:
        cursor, initial = live_contracts(con)
        assert len(initial) == len(contract.bins)
        # Old malformed bodies must never be decoded during an incremental refresh.
        con.execute("UPDATE market_captures SET payload_json='invalid' WHERE rowid<=?", (cursor,))
        assert live_contracts(con, cursor) == (cursor, [])
        con.execute(
            "INSERT INTO market_captures "
            "SELECT capture_id||'-new',source,kind,event_id,market_id,requested_at,?,"
            "content_hash||'-new',? FROM market_captures LIMIT 1",
            (datetime.now(UTC).isoformat(), json.dumps(bundle.gamma_snapshot.payload)),
        )
        next_cursor, current = live_contracts(con, cursor)
        assert next_cursor > cursor
        assert len(current) == len(contract.bins)
        assert all(r['source_id'].endswith('-new') for r in current)
        assert live_contracts(con, next_cursor) == (next_cursor, [])


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

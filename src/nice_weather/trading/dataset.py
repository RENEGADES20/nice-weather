from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from nice_weather.trading.storage import connect, digest, encoded


def timestamp(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return int(parsed.timestamp() * 1_000_000_000)


def contracts(con, cutoff: str, since: str = "1970-01-01T00:00:00+00:00") -> list[dict]:
    from nice_weather.config import load_city_config
    from nice_weather.contract import parse_gamma_contract

    # contract_bins is an upserted display table. Rebuild versions from immutable captures.
    captures = con.execute(
        """
        SELECT capture_id AS source_id,received_at,content_hash,payload_json FROM market_captures
        WHERE julianday(received_at)<=julianday(?) AND julianday(received_at)>=julianday(?)
        UNION ALL
        SELECT snapshot_id AS source_id,received_at,content_hash,payload_json FROM raw_snapshots
        WHERE julianday(received_at)<=julianday(?) AND julianday(received_at)>=julianday(?)
          AND payload_json LIKE '%"events"%'
          AND snapshot_id NOT IN (SELECT capture_id FROM market_captures)
        ORDER BY received_at,source_id
    """,
        (cutoff, since, cutoff, since),
    )
    result = []
    for capture in captures:
        payload = json.loads(capture["payload_json"])
        if not payload.get("events"):
            continue
        try:
            contract = parse_gamma_contract(payload, load_city_config())
        except (ValueError, KeyError, TypeError):
            continue
        markets = {str(m["id"]): m for m in payload["events"][0].get("markets", [])}
        for item in contract.bins:
            market = markets[item.market_id]
            known = market.get("feesEnabled") is False or all(
                k in (market.get("feeSchedule") or {}) for k in ("rate", "exponent")
            )
            result.append(
                asdict(item)
                | {
                    "event_id": contract.event_id,
                    "local_day": contract.local_day.isoformat(),
                    "station_id": contract.station_id,
                    "timezone": contract.timezone,
                    "observation_end": contract.observation_end.isoformat(),
                    "rule_hash": contract.rule_hash,
                    "rule_version": contract.rule_version,
                    "parse_status": contract.parse_status,
                    "ambiguities_json": encoded(contract.ambiguities),
                    "received_at": capture["received_at"],
                    "source_id": capture["source_id"],
                    "source_hash": capture["content_hash"],
                    "fee_known": known,
                }
            )
    return result


def export_dataset(
    source: Path, directory: Path, start: str, end: str, settlements_path: Path | None = None
) -> Path:
    begin, finish = timestamp(start), timestamp(end)
    if finish <= begin:
        raise ValueError("End must follow start")
    directory.mkdir(parents=True, exist_ok=True)
    # SQLite backup supplies one consistent source view; all queries use that copy.
    with connect(source, readonly=True) as original, sqlite3.connect(":memory:") as copy:
        original.backup(copy)
        copy.row_factory = sqlite3.Row
        definitions = contracts(copy, end)
        tokens = {row[key] for row in definitions for key in ("yes_token_id", "no_token_id")}
        rows = [
            dict(r)
            for r in copy.execute(
                """
            SELECT rowid AS source_seq,* FROM market_top_ticks
            WHERE julianday(received_at)>=julianday(?) AND julianday(received_at)<=julianday(?)
              AND source='clob_ws' AND event_kind IN ('quote','snapshot','disconnect')
            ORDER BY julianday(received_at),rowid
        """,
                (start, end),
            )
            if r["token_id"] in tokens
        ]
        events = [
            {
                "kind": "contract",
                "ts": max(begin, timestamp(r["received_at"])),
                "data": r,
                "source_seq": -len(definitions) + i,
            }
            for i, r in enumerate(definitions)
        ]
        events += [
            {
                "kind": "quote",
                "ts": timestamp(r["received_at"]),
                "data": r,
                "source_seq": r["source_seq"],
            }
            for r in rows
        ]
        if settlements_path and settlements_path.exists():
            with connect(settlements_path, readonly=True) as results:
                finals = [
                    json.loads(r[0])
                    for r in results.execute(
                        "SELECT body FROM inputs WHERE json_extract(body,'$.kind')='settlement' "
                        "ORDER BY json_extract(body,'$.ts'),run_id,seq"
                    )
                ]
            seen = set()
            for final in finals:
                token = final["data"]["token_id"]
                if token not in tokens or token in seen or final["ts"] > finish:
                    continue
                seen.add(token)
                events.append(
                    final
                    | {
                        "ts": max(begin, final["ts"]),
                        "source_seq": len(rows) + len(definitions) + len(seen),
                    }
                )
        events.sort(key=lambda e: (e["ts"], e["source_seq"]))
        payload = {
            "schema": 1,
            "source": str(source.resolve()),
            "start": start,
            "end": end,
            "clock": "received_at / source_seq",
            "execution": "L1 top depth approximation",
            "legacy_null_ticks": "excluded",
            "events": events,
        }
        payload["coverage"] = coverage(payload)
        payload["dataset_id"] = digest(payload)
        target = directory / (payload["dataset_id"] + ".json")
        if not target.exists():
            with target.open("x", encoding="utf-8") as handle:
                handle.write(encoded(payload))
    return target


def valid_quote(row: dict) -> bool:
    import math

    keys = ("best_bid", "best_ask", "bid_size", "ask_size")
    return (
        row.get("source") in {"clob_ws", "clob_rest"}
        and row.get("event_kind") in {"quote", "snapshot"}
        and row.get("status") in {"available", "reconnect_snapshot"}
        and all(type(row.get(k)) in (int, float) and math.isfinite(row[k]) for k in keys)
        and 0 < row["best_bid"] <= row["best_ask"] < 1
        and row["bid_size"] > 0
        and row["ask_size"] > 0
    )


def coverage(dataset: dict, tokens: list[str] | None = None, require_both=False) -> dict:
    counts, gaps, last = {}, {}, {}
    definitions = {}
    for event in dataset["events"]:
        row = event["data"]
        if event["kind"] == "contract":
            for key in ("yes_token_id", "no_token_id"):
                definitions[row[key]] = row
        elif event["kind"] == "quote" and valid_quote(row):
            token = row["token_id"]
            counts[token] = counts.get(token, 0) + 1
            if token in last:
                gaps[token] = max(gaps.get(token, 0), (event["ts"] - last[token]) / 1e9)
            last[token] = event["ts"]
    reasons = []
    selected = tokens if tokens is not None else list(counts)
    if not selected:
        reasons.append("No executable L1 quotes")
    for token in selected:
        if token not in definitions or not counts.get(token):
            reasons.append(f"Missing executable token history: {token}")
        elif require_both:
            for key in ("yes_token_id", "no_token_id"):
                if not counts.get(definitions[token][key]):
                    reasons.append(f"Missing {key} history for {token}")
    return {
        "quote_counts": counts,
        "max_gap_seconds": gaps,
        "rejection_reasons": reasons,
        "limitations": [
            "L1 only; no maker queue advantage",
            "Weather strategies not registered",
            "Unverified final outcomes remain unsettled",
        ],
    }


def load_dataset(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = payload.pop("dataset_id")
    if digest(payload) != expected:
        raise ValueError("Dataset content hash mismatch")
    payload["dataset_id"] = expected
    return payload

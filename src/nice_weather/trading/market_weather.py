"""Market-day display projections. These views never feed trading decisions."""

from __future__ import annotations

import calendar
import json
import math
import re
import time
from datetime import date, datetime

from nice_weather.trading.storage import connect, digest
from nice_weather.trading.us_markets import CLIMATE_ZONE, VENUES


def selection(venue, day=None):
    if venue not in VENUES:
        raise ValueError("Unsupported venue")
    if day is not None and date.fromisoformat(day).isoformat() != day:
        raise ValueError("Expected YYYY-MM-DD")


def epoch(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return float(value) if math.isfinite(value) else None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo else None
    except (AttributeError, TypeError, ValueError):
        return None


def catalog(path, venue):
    selection(venue)
    by_day = {}
    with connect(path, readonly=True) as con:
        if con.execute("SELECT 1 FROM sqlite_master WHERE name='settlement_watch'").fetchone():
            for row in con.execute("SELECT day,contracts FROM settlement_watch WHERE venue=?",
                                   (venue,)):
                by_day[row["day"]] = {c["yes_token_id"]: c for c in json.loads(row["contracts"])
                                      if c.get("station_id") == "KNYC" and c.get("venue") == venue}
        # publish() keeps every market day in settlement_watch. Re-reading the
        # repeated contract captures on every chart/quote request stalls the VM.
        # Legacy databases without that projection still use their captured facts.
        rows = [] if by_day else list(con.execute(
            "SELECT seq,received,body FROM feed_events WHERE kind='contracts' AND key=? "
            "ORDER BY seq", (venue,)))
        rows.extend(con.execute(
            "SELECT seq,received,body FROM feed_latest "
            "WHERE kind='market_directory' AND key LIKE ? ORDER BY seq", (venue + ":%",)))
        for row in rows:
            groups = {}
            for contract in json.loads(row["body"]):
                if contract.get("venue") != venue or contract.get("station_id") != "KNYC":
                    continue
                day = contract["local_day"]
                group = groups.setdefault(day, {})
                group[contract["yes_token_id"]] = contract
            by_day.update(groups)
        days = []
        for day, group in sorted(by_day.items()):
            if not group:
                continue
            contracts = sorted(group.values(), key=lambda c: (
                -math.inf if c.get("lower") is None else c["lower"], c["yes_token_id"]))
            has_prices = any(con.execute(
                "SELECT 1 FROM feed_events WHERE kind='book' AND key=? LIMIT 1", (token,)
            ).fetchone() for token in group)
            days.append({"day": day, "contracts": contracts,
                         "listed": True, "has_prices": has_prices,
                         "status": "open" if any(c.get("active") for c in contracts)
                         else "closed",
                         "received_at": max(c.get("received_at", 0) for c in contracts)})
        health = con.execute("SELECT body FROM feed_latest WHERE kind='health' AND key=?",
                             (f"{venue}_directory",)).fetchone()
    return {"venue": venue, "station_id": "KNYC", "days": days,
            "discovery": json.loads(health["body"]) if health else {"status": "not_collected"}}


def market_day(path, venue, day):
    selection(venue, day)
    found = next((r for r in catalog(path, venue)["days"] if r["day"] == day), None)
    contracts = found["contracts"] if found else []
    windows = {(c.get("observation_start"), c.get("observation_end")) for c in contracts}
    start, end = next(iter(windows)) if len(windows) == 1 else (None, None)
    return {"venue": venue, "station_id": "KNYC", "day": day, "contracts": contracts,
            "observation_start": start, "observation_end": end,
            "display_timezone": "America/New_York",
            "window_verified": bool(contracts) and all(
                c.get("parse_status") == "parsed" for c in contracts),
            "reason": "NO_CONTRACTS" if not contracts else
            ("OBSERVATION_WINDOW_UNAVAILABLE" if not start or not end else None),
            "has_prices": bool(found and found["has_prices"])}


def cli_day(text):
    match = re.search(r"CENTRAL PARK.*?CLIMATE SUMMARY FOR (\w+)\s+(\d+)\s+(\d{4})", text)
    if not match:
        return None
    months = {name.upper(): i for i, name in enumerate(calendar.month_name) if name}
    try:
        return date(int(match[3]), months[match[1]], int(match[2])).isoformat()
    except (KeyError, ValueError):
        return None


def project_weather(con, seq, source, body, received):
    """Compact display facts in feed order; retain every actual value revision."""
    if body.get("station") != "KNYC":
        return
    capture = body.get("capture_id")
    item = 0

    def add(at, value, receipt, version, point_capture=None, issued=None, event_received=None):
        nonlocal item
        item += 1
        at, receipt = epoch(at), epoch(receipt)
        if (at is None or not isinstance(value, (float, int))
                or isinstance(value, bool) or not math.isfinite(value)):
            return
        forecast = source in {"hrrr", "nws_forecast"}
        forecast_key = digest([source, at, version, value]) if forecast else None
        if forecast:
            previous = con.execute(
                "SELECT seq,item,body FROM weather_chart_points WHERE forecast_key=?",
                (forecast_key,),
            ).fetchone()
            if previous:
                old = json.loads(previous["body"])
                if receipt is None or (old["received_at"] is not None
                                       and old["received_at"] <= receipt):
                    return
        else:
            previous = con.execute(
                "SELECT body FROM weather_chart_points WHERE source=? AND object_time=? "
                "ORDER BY seq DESC,item DESC LIMIT 1", (source, at),
            ).fetchone()
            if previous:
                old = json.loads(previous["body"])
                if old["version"] == str(version) and old["value"] == value:
                    return
                # A -> B -> A becomes known again at the later event's real receipt.
                receipt = epoch(event_received) or receipt
        point = {"time": at, "value": value, "received_at": receipt,
                 "version": str(version), "capture_id": point_capture, "source": source,
                 "issued_at": epoch(issued), "unit": "F"}
        text = json.dumps(point, separators=(",", ":"), allow_nan=False)
        if forecast and previous:
            con.execute("UPDATE weather_chart_points SET body=? WHERE seq=? AND item=?",
                        (text, previous["seq"], previous["item"]))
        else:
            con.execute("INSERT INTO weather_chart_points VALUES (?,?,?,?,?,?)",
                        (seq, item, source, at, forecast_key, text))

    if source == "metar":
        for p in body.get("data", []):
            if p.get("icaoId") == "KNYC" and isinstance(p.get("temp"), (float, int)):
                add(p.get("obsTime"), p["temp"] * 1.8 + 32,
                    p.get("first_received_at", received),
                    p.get("revision_id", p.get("rawOb", str(p["temp"]))), capture,
                    event_received=received)
    elif source == "nws_observations":
        for entry in body.get("data", {}).get("features", []):
            p = entry.get("properties", {})
            if p.get("station", "").rstrip("/").split("/")[-1] != "KNYC":
                continue
            temperature = p.get("temperature", {})
            value, unit = temperature.get("value"), temperature.get("unitCode")
            if isinstance(value, (int, float)) and unit in {"wmoUnit:degC", "wmoUnit:degF"}:
                add(p.get("timestamp"), value * 1.8 + 32 if unit.endswith("degC")
                    else value, received, p.get("rawMessage") or str(value), capture)
    elif source in {"hrrr", "nws_forecast"}:
        issued = body.get("cycle", body.get("issued_at"))
        version = digest({"issued": issued,
                          "points": [(p.get("valid_at"), p.get("temperature_f"))
                                     for p in body.get("points", [])]})
        for p in body.get("points", []):
            add(p.get("valid_at"), p.get("temperature_f"),
                max(received, epoch(p.get("received_at")) or received), version,
                p.get("index_capture", capture), issued)
    elif source == "hourly_temp":
        for p in body.get("points", []):
            add(p.get("observed_at"), p.get("temperature_f"), received,
                p.get("revision_id", str(p.get("temperature_f"))), capture)
    elif source == "cli":
        day = cli_day(body.get("text") or "")
        if day:
            con.execute("INSERT OR IGNORE INTO weather_chart_cli VALUES (?,?,?,?)",
                        (digest([body.get("issued_at"), body.get("text")]), day, seq,
                         json.dumps(body | {"received_at": received, "day": day})))


def prepare_weather_history(path):
    """Backfill in feed order using the existing small, covering weather index."""
    with connect(path, readonly=True) as con:
        con.execute("BEGIN")  # Source keys and the upper bound share one snapshot.
        upper = con.execute(
            "SELECT COALESCE(MAX(seq),0) FROM feed_events INDEXED BY feed_history "
            "WHERE kind='weather'",
        ).fetchone()[0]
        cursor = con.execute("SELECT seq FROM weather_chart_progress WHERE id=1").fetchone()[0]
        sources = [row[0] for row in con.execute(
            "SELECT DISTINCT key FROM feed_events INDEXED BY feed_history WHERE kind='weather'")]
    while cursor < upper:
        with connect(path, readonly=True) as con:
            con.execute("BEGIN")
            cursor = con.execute("SELECT seq FROM weather_chart_progress WHERE id=1").fetchone()[0]
            if cursor >= upper:
                break
            # A per-source seek uses (kind,key,seq); merge before fetching any bodies.
            # Creating a new partial index would scan every large market snapshot.
            seqs = sorted(row[0] for source in sources for row in con.execute(
                "SELECT seq FROM feed_events INDEXED BY feed_history "
                "WHERE kind='weather' AND key=? AND seq>? AND seq<=? ORDER BY seq LIMIT 64",
                (source, cursor, upper),
            ))[:64]
            rows = con.execute(
                "SELECT seq,key,received,body FROM feed_events WHERE seq IN ("
                + ",".join("?" for _ in seqs) + ") ORDER BY seq", seqs,
            ).fetchall() if seqs else []
        decoded = [(row, json.loads(row["body"])) for row in rows]
        with connect(path) as con:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute("SELECT seq FROM weather_chart_progress WHERE id=1").fetchone()[0]
            if current != cursor:
                cursor = current  # Another reader/collector committed while bodies were loaded.
                continue
            for row, body in decoded:
                project_weather(con, row["seq"], row["key"], body, row["received"])
            cursor = rows[-1]["seq"] if rows else max(cursor, upper)
            con.execute("UPDATE weather_chart_progress SET seq=? WHERE id=1", (cursor,))


def weather_history(path, venue, day):
    context = market_day(path, venue, day)
    # A display-only climate window is labelled when no contract window is available.
    start = epoch(context["observation_start"])
    end = epoch(context["observation_end"])
    window_source = ("合约观察窗口" if context["window_verified"] else
                     "合约归一化窗口（规则待核验）")
    if start is None or end is None:
        start = datetime.combine(date.fromisoformat(day), datetime.min.time(),
                                 CLIMATE_ZONE).timestamp()
        end = start + 86400
        window_source = "KNYC climate day; contract window unavailable"
    prepare_weather_history(path)
    sources = {key: [] for key in ("metar", "nws_observations", "hourly_temp", "hrrr",
                                  "nws_forecast")}
    with connect(path, readonly=True) as con:
        # Object time chooses the market window. Late receipts and revisions stay visible.
        for row in con.execute(
                "SELECT source,body FROM weather_chart_points "
                "WHERE object_time>=? AND object_time<? ORDER BY object_time,seq,item",
                (start, end)):
            sources[row["source"]].append(json.loads(row["body"]))
        reports = [json.loads(row["body"]) for row in con.execute(
            "SELECT body FROM weather_chart_cli WHERE day=? ORDER BY seq", (day,))]
    return context | {"start": start, "end": end, "window_source": window_source,
                      "as_of": time.time(),
                      "sources": {key: sorted(points, key=lambda p: (
                          p["time"], p["received_at"] or 0)) for key, points in sources.items()},
                      "cli": reports,
                      "missing_sources": [key for key, points in sources.items() if not points]}


def scoped_history(feed, venue, day, token, before=None):
    context = market_day(feed.path, venue, day)
    if token not in {c["yes_token_id"] for c in context["contracts"]}:
        raise ValueError("Token does not belong to the requested venue/market day")
    # Project display probabilities and top levels without decoding captured depth.
    # Legacy rows lacking a venue probability remain gaps; never synthesize a midpoint.
    with connect(feed.path, readonly=True) as con:
        rows = con.execute(
            "SELECT seq,received,"
            "CASE WHEN json_type(body,'$.received_at') IS NULL THEN received "
            "ELSE json_extract(body,'$.received_at') END AS receipt,"
            "json_extract(body,'$.exchange_time') AS exchange_time,"
            "json_extract(body,'$.probability') AS probability,"
            "json_extract(body,'$.probability_source') AS probability_source,"
            "json_extract(body,'$.probability_time') AS probability_time,"
            "json_extract(body,'$.probability_received_at') AS probability_received_at,"
            "json_extract(body,'$.probability_capture_id') AS probability_capture_id,"
            "json_extract(body,'$.capture_id') AS capture_id,"
            "json_extract(body,'$.bids[0]') AS bid,"
            "json_extract(body,'$.asks[0]') AS ask "
            "FROM feed_events WHERE kind='book' AND key=? AND seq<? "
            "ORDER BY seq DESC LIMIT 300", (token, before or 2**63 - 1),
        ).fetchall()
    points = [{"seq": r["seq"], "time": r["received"],
                        "received_at": r["receipt"],
                        "exchange_time": r["exchange_time"], "source": "public_book",
                        **{key: r[key] for key in ("probability", "probability_source",
                            "probability_time", "probability_received_at",
                            "probability_capture_id", "capture_id")},
                        "bids": [json.loads(r["bid"])] if r["bid"] else [],
                        "asks": [json.loads(r["ask"])] if r["ask"] else []}
                       for r in reversed(rows)]
    contract = next(c for c in context["contracts"] if c["yes_token_id"] == token)
    return {"venue": venue, "day": day, "token": token,
            "points": feed.restore_probabilities(points, contract),
            "next_before": rows[-1]["seq"] if len(rows) == 300 else None,
            "reason": None if rows else "NO_PRICE_HISTORY"}

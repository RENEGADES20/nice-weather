"""Read-only, market-day projections. These views never feed trading decisions."""

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
    sources = {key: {} for key in ("metar", "nws_observations", "hourly_temp", "hrrr",
                                  "nws_forecast")}
    reports = {}
    latest_observations = {}

    def add(source, at, value, received, version, capture, issued=None, event_received=None):
        at, received = epoch(at), epoch(received)
        if at is None or not start <= at < end or not isinstance(value, (float, int)):
            return
        if isinstance(value, bool) or not math.isfinite(value):
            return
        # Repeated polls keep the first actual receipt of the same source version.
        key = (at, version, value)
        if source not in {"hrrr", "nws_forecast"}:
            previous = latest_observations.get((source, at))
            if previous and previous["version"] == str(version) and previous["value"] == value:
                return
            if previous:
                # A -> B -> A is a later revision, not a duplicate of the first A.
                received = epoch(event_received) or received
            key = (*key, received)
        point = {"time": at, "value": value, "received_at": received,
                 "version": str(version), "capture_id": capture, "source": source,
                 "issued_at": epoch(issued), "unit": "F"}
        if source not in {"hrrr", "nws_forecast"}:
            latest_observations[(source, at)] = point
        old = sources[source].get(key)
        if old is None or (received is not None and (
                old["received_at"] is None or received < old["received_at"])):
            sources[source][key] = point

    with connect(path, readonly=True) as con:
        # Scan weather facts only. Late reports/revisions and pre-day forecasts must survive.
        for row in con.execute("SELECT key,seq,received,body FROM feed_events "
                               "WHERE kind='weather' ORDER BY seq"):
            body = json.loads(row["body"])
            if body.get("station") != "KNYC":
                continue
            source, received = row["key"], row["received"]
            capture = body.get("capture_id")
            if source == "metar":
                for p in body.get("data", []):
                    if p.get("icaoId") == "KNYC" and isinstance(p.get("temp"), (float, int)):
                        add(source, p.get("obsTime"), p["temp"] * 1.8 + 32,
                            p.get("first_received_at", received),
                            p.get("revision_id", p.get("rawOb", str(p["temp"]))), capture,
                            event_received=received)
            elif source == "nws_observations":
                for item in body.get("data", {}).get("features", []):
                    p = item.get("properties", {})
                    station = p.get("station", "").rstrip("/").split("/")[-1]
                    if station != "KNYC":
                        continue
                    temperature = p.get("temperature", {})
                    value, unit = temperature.get("value"), temperature.get("unitCode")
                    if isinstance(value, (int, float)) and unit in {"wmoUnit:degC", "wmoUnit:degF"}:
                        add(source, p.get("timestamp"), value * 1.8 + 32 if unit.endswith("degC")
                            else value, received, p.get("rawMessage") or str(value), capture)
            elif source in {"hrrr", "nws_forecast"}:
                version = digest({"issued": body.get("cycle", body.get("issued_at")),
                                  "points": [(p.get("valid_at"), p.get("temperature_f"))
                                             for p in body.get("points", [])]})
                for p in body.get("points", []):
                    add(source, p.get("valid_at"), p.get("temperature_f"),
                        max(received, epoch(p.get("received_at")) or received),
                        version,
                        p.get("index_capture", capture), body.get("cycle", body.get("issued_at")))
            elif source == "hourly_temp":
                for p in body.get("points", []):
                    add(source, p.get("observed_at"), p.get("temperature_f"), received,
                        p.get("revision_id", str(p.get("temperature_f"))), capture)
            elif source == "cli" and cli_day(body.get("text", "")) == day:
                key = (body.get("issued_at"), body.get("text"))
                reports.setdefault(key, body | {"received_at": received, "day": day})
    return context | {"start": start, "end": end, "window_source": window_source,
                      "as_of": time.time(),
                      "sources": {key: sorted(points.values(), key=lambda p: (
                          p["time"], p["received_at"] or 0)) for key, points in sources.items()},
                      "cli": list(reports.values()),
                      "missing_sources": [key for key, points in sources.items() if not points]}


def scoped_history(feed, venue, day, token, before=None):
    context = market_day(feed.path, venue, day)
    if token not in {c["yes_token_id"] for c in context["contracts"]}:
        raise ValueError("Token does not belong to the requested venue/market day")
    rows = feed.history(token, before, limit=1500)
    return {"venue": venue, "day": day, "token": token,
            "points": [{"seq": r["seq"], "time": r["time"],
                        "received_at": r.get("received_at", r["time"]),
                        "exchange_time": r.get("exchange_time"), "source": "public_book",
                        "bids": r.get("bids", [])[:1], "asks": r.get("asks", [])[:1]}
                       for r in rows],
            "next_before": rows[0]["seq"] if len(rows) == 1500 else None,
            "reason": None if rows else "NO_PRICE_HISTORY"}

"""KNYC public market normalization. No account credentials or order APIs."""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from nice_weather.trading.storage import digest

VENUES = ("kalshi", "poly_us")
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
POLY_US = "https://gateway.polymarket.us/v1"
CLIMATE_ZONE = timezone(timedelta(hours=-5))


def event_url(venue, day):
    value = date.fromisoformat(day)
    if venue == "kalshi":
        # Locale-independent month names are part of the venue identifier.
        month = (
            "JAN",
            "FEB",
            "MAR",
            "APR",
            "MAY",
            "JUN",
            "JUL",
            "AUG",
            "SEP",
            "OCT",
            "NOV",
            "DEC",
        )[value.month - 1]
        ticker = f"KXHIGHNY-{value.year % 100:02}{month}{value.day:02}"
        return f"{KALSHI}/markets?event_ticker={ticker}&limit=100"
    if venue == "poly_us":
        return f"{POLY_US}/events/slug/temp-nychigh-{value.isoformat()}"
    raise ValueError("Unsupported venue")


def bounds(title):
    title = title.strip().lower().replace("°", "")
    if match := re.fullmatch(r"(-?\d+) (?:or )?below", title):
        return None, int(match[1])
    if match := re.fullmatch(r"(-?\d+) (?:or )?above", title):
        return int(match[1]), None
    if match := re.fullmatch(r"(-?\d+)\s*(?:to|-)\s*(-?\d+)", title):
        lo, hi = map(int, match.groups())
        if lo <= hi:
            return lo, hi
    raise ValueError("Ambiguous temperature bin")


def normalize(venue, day, payload, received_at, series=None):
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), CLIMATE_ZONE)
    rows = payload["markets"] if venue == "kalshi" else payload["event"]["markets"]
    output = []
    for raw in rows:
        if venue == "kalshi":
            key, title = raw["ticker"], raw["yes_sub_title"]
            expected = parse_qs(urlsplit(event_url(venue, day)).query)["event_ticker"][0]
            if raw.get("event_ticker") != expected or not key.startswith(expected + "-"):
                raise ValueError("Kalshi event/day mismatch")
            primary = raw.get("rules_primary", "")
            rules = primary + "\n" + raw.get("rules_secondary", "")
            if "New York City (CLINYC)" not in primary or "maximum temperature" not in primary:
                raise ValueError("Unrecognized Kalshi station/metric")
            source = "weather_company" if "according to The Weather Company" in primary else None
            fee_known = bool(series and series.get("fee_type") == "quadratic")
            rate = 0.07 * float(series.get("fee_multiplier", 1)) if series else 0
            tick = raw.get("price_ranges", [{}])[0].get("step")
            known_tick = (
                raw.get("price_level_structure") == "linear_cent"
                and tick == "0.0100"
                and len(raw.get("price_ranges", [])) == 1
            )
            close = raw["close_time"]
            active = raw["status"] == "active"
            minimum = 0.01
            rounding = "ceil_cent"
        elif venue == "poly_us":
            key, title, rules = raw["slug"], raw["title"], raw["description"]
            if not key.startswith(f"tc-temp-nychigh-{day}-") or "Central Park (KNYC)" not in rules:
                raise ValueError("Unrecognized Poly US station/day")
            source = "nws_cli" if "Climatological Report" in rules else None
            rate = float(raw.get("feeCoefficient", -1))
            # Official schedule verified 2026-09-19. New coefficients need independent review.
            fee_known = day >= "2026-09-17" and rate == 0.0695
            tick = raw.get("orderPriceMinTickSize")
            known_tick = tick == 0.01
            close = raw["endDate"]
            active = raw.get("active") is True and raw.get("closed") is False
            minimum = raw.get("minimumTradeQty", 0.01)
            rounding = "poly_us_order_half_even_v1"
        else:
            raise ValueError("Unsupported venue")
        lower, upper = bounds(title)
        if (
            not math.isfinite(rate)
            or rate < 0
            or not math.isfinite(float(minimum))
            or float(minimum) <= 0
        ):
            raise ValueError("Invalid market fees/minimum")
        # Prose and API close times are not sufficient proof of the observation window,
        # rounding and post-final corrections. Capture and display; execution fails closed.
        ambiguities = ["observation window, rounding and revision rules awaiting venue audit"]
        if venue == "poly_us" and float(minimum) < 1:
            ambiguities.append("API minimum quantity conflicts with official whole-contract rule")
        if not source or not known_tick:
            ambiguities.append("unknown source or tick")
        token = venue + ":" + key
        output.append(
            {
                "venue": venue,
                "station_id": "KNYC",
                "local_day": day,
                "event_id": f"{venue}:KNYC:{day}",
                "condition_id": key,
                "yes_token_id": token,
                "no_token_id": token + ":NO",
                "question": title,
                "title": title,
                "bin_label": title,
                "label": title,
                "lower": lower,
                "upper": upper,
                "unit": "F",
                "timezone": "America/New_York",
                "climate_timezone": "UTC-05:00",
                "settlement_source": source,
                "observation_start": start.astimezone(UTC).isoformat(),
                "observation_end": (start + timedelta(days=1)).astimezone(UTC).isoformat(),
                "close_time": close,
                "active": active,
                "closed": not active,
                "accepting_orders": active,
                "tick_size": str(tick),
                "minimum_order_size": minimum,
                "quantity_step": "0.01",
                "fee_known": fee_known,
                "fee_rate": rate,
                "fee_exponent": 1,
                "fee_rounding": rounding,
                "rules": rules,
                "rules_version": digest({"rules": rules, "close": close, "fee_rate": rate,
                                         "fee_rounding": rounding, "minimum": minimum}),
                "received_at": received_at,
                "parse_status": "ambiguous",
                "ambiguities_json": json.dumps(ambiguities),
            }
        )
    return sorted(output, key=lambda c: -math.inf if c["lower"] is None else c["lower"])


def book_url(contract):
    key = contract["condition_id"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
        raise ValueError("Invalid market identifier")
    if contract["venue"] == "kalshi":
        return f"{KALSHI}/markets/{key}/orderbook?depth=0"
    return f"{POLY_US}/markets/{key}/book"


def final_value(contract, evidence, received):
    """Validate actual venue finality and return YES payout; closed alone is insufficient."""
    raw = evidence["market"]
    key, venue = contract["condition_id"], contract["venue"]
    if not math.isfinite(received):
        raise ValueError("Invalid settlement receipt")
    if venue == "kalshi":
        if (raw.get("ticker") != key or raw.get("status") != "finalized"
                or raw.get("is_provisional") is True):
            raise ValueError("Kalshi settlement not final or wrong market")
        stamp = datetime.fromisoformat(raw["settlement_ts"].replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.timestamp() > received:
            raise ValueError("Future or unzoned settlement")
        value = Decimal(str(raw["settlement_value_dollars"]))
        if raw.get("result") in {"yes", "no"} and value != int(raw["result"] == "yes"):
            raise ValueError("Inconsistent Kalshi final outcome")
    elif venue == "poly_us":
        confirmation = evidence["confirmation"]
        if (raw.get("slug") != key or raw.get("status") != "MARKET_STATUS_RESOLVED"
                or raw.get("ep3Status") != "EXPIRED" or raw.get("closed") is not True
                or confirmation.get("slug") != key):
            raise ValueError("Poly US settlement not final or wrong market")
        value = Decimal(str(confirmation["settlement"]))
        labels, prices = json.loads(raw["outcomes"]), json.loads(raw["outcomePrices"])
        if (labels != ["Yes", "No"] or len(prices) != 2
                or Decimal(prices[0]) != value or Decimal(prices[1]) != 1 - value):
            raise ValueError("Inconsistent Poly US final outcome")
    else:
        raise ValueError("Unsupported settlement venue")
    if not value.is_finite() or not 0 <= value <= 1:
        raise ValueError("Invalid final payout")
    return value


def normalize_book(venue, payload, received_at):
    def levels(rows):
        output = []
        for p, q in rows:
            p, q = float(p), float(q)
            if not math.isfinite(p) or not math.isfinite(q) or not 0 <= p <= 1 or q < 0:
                raise ValueError("Invalid order book")
            if q:
                output.append([p, q])
        return output

    if venue == "kalshi":
        book = payload["orderbook_fp"]
        bids = levels(book.get("yes_dollars") or [])
        asks = levels([(str(1 - Decimal(p)), q) for p, q in book.get("no_dollars") or []])
        exchange_time = None
    else:
        book = payload["marketData"]
        bids = levels([(r["px"]["value"], r["qty"]) for r in book.get("bids", [])])
        asks = levels([(r["px"]["value"], r["qty"]) for r in book.get("offers", [])])
        exchange_time = book.get("transactTime")
    if bids and asks and max(p for p, _ in bids) >= min(p for p, _ in asks):
        raise ValueError("Crossed or locked book")
    return {
        "bids": sorted(bids, reverse=True),
        "asks": sorted(asks),
        "received_at": received_at,
        "exchange_time": exchange_time,
        "complete": True,
    }

from __future__ import annotations

import gzip
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from nice_weather.config import CityConfig
from nice_weather.domain import (
    SettlementEvidence,
    SourceCapture,
    content_hash,
    stable_id,
    utc_now,
)
from nice_weather.store import WeatherStore

NWS_USER_AGENT = "nice-weather/0.1 (github.com/RENEGADES20/nice-weather)"


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def celsius_to_fahrenheit(value: float) -> float:
    return value * 9.0 / 5.0 + 32.0


@dataclass(frozen=True)
class HttpPayload:
    payload: Any
    raw_bytes: bytes
    request_url: str
    status_code: int
    content_type: str
    requested_at: datetime
    received_at: datetime


@dataclass(frozen=True)
class PagePayload:
    html: str
    text: str
    screenshot_png: bytes | None
    request_url: str
    requested_at: datetime
    received_at: datetime
    status_code: int = 200
    response_headers: dict[str, str] | None = None


@dataclass(frozen=True)
class ParsedSettlementPage:
    table_text: str
    tmax_f: float | None
    page_updated_at: datetime | None
    first_next_day_observed_at: datetime | None
    first_next_day_temperature_f: float | None
    parse_status: str
    no_trade_reason: str | None
    finalized: bool
    rows: tuple[tuple[datetime, float], ...]


def settlement_screenshot_trigger(
    parsed: ParsedSettlementPage,
    previous_tmax: float | None,
    previous_finalized: bool,
) -> str | None:
    if parsed.parse_status != "parsed":
        return "parse_failure"
    if parsed.finalized and not previous_finalized:
        return "first_finalized"
    if previous_tmax is not None and parsed.tmax_f is not None and parsed.tmax_f < previous_tmax:
        return "non_monotonic_tmax"
    if previous_finalized and parsed.tmax_f != previous_tmax:
        return "post_final_change"
    return None


class OfficialWeatherClient:
    awc_url = "https://aviationweather.gov/api/data/metar"
    nws_url = "https://api.weather.gov"

    def __init__(self, timeout: float = 30.0, retries: int = 3) -> None:
        self.retries = retries
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": NWS_USER_AGENT,
                "Accept": "application/geo+json, application/json",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> OfficialWeatherClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get_json(self, url: str, *, params: dict[str, str] | None = None) -> HttpPayload:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            requested_at = utc_now()
            try:
                response = self.client.get(url, params=params)
                response.raise_for_status()
                return HttpPayload(
                    payload=response.json(),
                    raw_bytes=response.content,
                    request_url=str(response.request.url),
                    status_code=response.status_code,
                    content_type="application/json",
                    requested_at=requested_at,
                    received_at=utc_now(),
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
        assert last_error is not None
        raise last_error

    def fetch_metar(self, station_id: str) -> HttpPayload:
        result = self._get_json(
            self.awc_url,
            params={"ids": station_id, "format": "json", "taf": "false", "hours": "3"},
        )
        if not isinstance(result.payload, list):
            raise ValueError("AviationWeather METAR response must be a JSON list")
        return result

    def fetch_hourly_forecast(self, config: CityConfig) -> HttpPayload:
        points = self._get_json(
            f"{self.nws_url}/points/{config.latitude:.7f},{config.longitude:.7f}"
        )
        url = points.payload.get("properties", {}).get("forecastHourly")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValueError("NWS points response does not contain forecastHourly")
        return self._get_json(url)

    def fetch_nws_observations(self, config: CityConfig, now: datetime) -> HttpPayload:
        local_start = datetime.combine(now.astimezone(config.zone).date(), datetime_time.min)
        local_start = local_start.replace(tzinfo=config.zone).astimezone(UTC)
        overlap_start = now.astimezone(UTC) - timedelta(
            hours=config.collector.nws_observation_overlap_hours
        )
        request_start = max(local_start, overlap_start)
        return self._get_json(
            f"{self.nws_url}/stations/{config.station_id}/observations",
            params={
                "start": request_start.isoformat().replace("+00:00", "Z"),
                "end": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            },
        )


class SettlementPageClient:
    """Render the public Weather.gov page; browser dependency stays optional at import time."""

    def fetch(self, url: str, *, screenshot: bool = False) -> PagePayload:
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Settlement evidence requires the collector extra and Playwright Chromium"
            ) from exc
        requested_at = utc_now()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            response = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            try:
                page.wait_for_function(
                    """
                    () => {
                      const node = document.querySelector('#OBS');
                      return node && node.innerText.trim().length > 100;
                    }
                    """,
                    timeout=30_000,
                )
                toggle = page.locator("#dataToggle")
                if "Show Hourly Data" in toggle.inner_text():
                    toggle.click()
                    page.wait_for_timeout(1_500)
                observation_table = page.locator("#OBS")
                text = observation_table.inner_text()
                screenshot_png = observation_table.screenshot(type="png") if screenshot else None
            except PlaywrightTimeoutError:
                page.wait_for_timeout(2_000)
                text = page.locator("body").inner_text()
                screenshot_png = page.screenshot(full_page=True, type="png") if screenshot else None
            html = page.content()
            final_url = page.url
            browser.close()
        return PagePayload(
            html,
            text,
            screenshot_png,
            final_url,
            requested_at,
            utc_now(),
            response.status if response else 200,
            response.headers if response else {},
        )


_TMAX_PATTERNS = (
    re.compile(
        r"(?:Tmax|Maximum Temperature|Max Temp(?:erature)?)\s*[:=]?\s*"
        r"(-?\d+(?:\.\d+)?)\s*°?\s*F",
        re.I,
    ),
    re.compile(r"Daily Maximum\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*°?\s*F", re.I),
)
_UPDATED_RE = re.compile(
    r"(?:Updated|Last Update(?:d)?)\s*[:=]?\s*"
    r"(\d{4}-\d{2}-\d{2}[ T]\d{1,2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)",
    re.I,
)
_ROW_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\s+"
    r"(?P<time>\d{1,2}:\d{2})[^\n]*?"
    r"(?P<temp>-?\d+(?:\.\d+)?)\s*°?\s*F\b",
    re.I,
)
_NWS_DISPLAY_ROW_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),\s+"
    r"(?P<time>\d{1,2}:\d{2})\s+(?P<ampm>am|pm)\s+"
    r"(?P<temp>-?\d+(?:\.\d+)?)\b",
    re.I,
)


def _parse_row_date(value: str) -> date:
    if "-" in value:
        return date.fromisoformat(value)
    month, day, year = (int(part) for part in value.split("/"))
    if year < 100:
        year += 2000
    return date(year, month, day)


def _display_row_date(month_name: str, day: int, local_day: date) -> date:
    month = datetime.strptime(month_name.title(), "%b").month
    candidates = [date(year, month, day) for year in range(local_day.year - 1, local_day.year + 2)]
    return min(candidates, key=lambda value: abs((value - local_day).days))


def parse_settlement_page(text: str, local_day: date, zone: ZoneInfo) -> ParsedSettlementPage:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    hourly_index = normalized.lower().find("hourly data")
    table_text = normalized[hourly_index:] if hourly_index >= 0 else normalized
    tmax_f = None
    for pattern in _TMAX_PATTERNS:
        match = pattern.search(table_text)
        if match:
            tmax_f = float(match.group(1))
            break
    rows: list[tuple[datetime, float]] = []
    for match in _ROW_RE.finditer(table_text):
        row_date = _parse_row_date(match.group("date"))
        hour, minute = (int(part) for part in match.group("time").split(":"))
        rows.append(
            (
                datetime.combine(row_date, datetime_time(hour, minute), tzinfo=zone),
                float(match.group("temp")),
            )
        )
    for line in table_text.splitlines():
        match = _NWS_DISPLAY_ROW_RE.match(line)
        if match is None:
            continue
        row_date = _display_row_date(
            match.group("month"), int(match.group("day")), local_day
        )
        hour, minute = (int(part) for part in match.group("time").split(":"))
        if match.group("ampm").lower() == "pm" and hour != 12:
            hour += 12
        if match.group("ampm").lower() == "am" and hour == 12:
            hour = 0
        rows.append(
            (
                datetime.combine(row_date, datetime_time(hour, minute), tzinfo=zone),
                float(match.group("temp")),
            )
        )
    local_temperatures = [temp for observed_at, temp in rows if observed_at.date() == local_day]
    if tmax_f is None and local_temperatures:
        tmax_f = max(local_temperatures)
    next_day_rows = [item for item in rows if item[0].date() > local_day]
    next_day = min(next_day_rows, key=lambda item: item[0]) if next_day_rows else None
    updated_match = _UPDATED_RE.search(normalized)
    page_updated_at = parse_time(updated_match.group(1)) if updated_match else None
    if not table_text or (tmax_f is None and not rows):
        return ParsedSettlementPage(
            table_text=table_text[:100_000],
            tmax_f=None,
            page_updated_at=page_updated_at,
            first_next_day_observed_at=None,
            first_next_day_temperature_f=None,
            parse_status="ambiguous",
            no_trade_reason="SETTLEMENT_PAGE_UNPARSEABLE",
            finalized=False,
            rows=tuple(rows),
        )
    return ParsedSettlementPage(
        table_text=table_text[:100_000],
        tmax_f=tmax_f,
        page_updated_at=page_updated_at,
        first_next_day_observed_at=next_day[0].astimezone(UTC) if next_day else None,
        first_next_day_temperature_f=next_day[1] if next_day else None,
        parse_status="parsed",
        no_trade_reason=None,
        finalized=next_day is not None,
        rows=tuple(rows),
    )


_METAR_TIME_RE = re.compile(r"\b(?P<day>\d{2})(?P<hour>\d{2})(?P<minute>\d{2})Z\b")


def parse_metar_observed_at(raw_message: str, reference: datetime) -> datetime:
    match = _METAR_TIME_RE.search(raw_message)
    if match is None:
        raise ValueError("METAR raw message does not contain DDHHMMZ")
    reference = reference.astimezone(UTC)
    candidates = []
    for month_offset in (-1, 0, 1):
        month_index = reference.year * 12 + reference.month - 1 + month_offset
        year, month_zero = divmod(month_index, 12)
        try:
            candidates.append(
                datetime(
                    year,
                    month_zero + 1,
                    int(match.group("day")),
                    int(match.group("hour")),
                    int(match.group("minute")),
                    tzinfo=UTC,
                )
            )
        except ValueError:
            continue
    if not candidates:
        raise ValueError("METAR DDHHMMZ cannot be mapped to a valid calendar date")
    observed_at = min(candidates, key=lambda value: abs(value - reference))
    if abs(observed_at - reference) > timedelta(days=20):
        raise ValueError("METAR DDHHMMZ is ambiguous relative to provider receipt time")
    return observed_at


def _json_capture(
    result: HttpPayload,
    *,
    source: str,
    kind: str,
    config: CityConfig,
    source_time: datetime | None = None,
    observed_at: datetime | None = None,
    issued_at: datetime | None = None,
    source_version: str | None = None,
) -> SourceCapture:
    payload_hash = content_hash(result.payload)
    return SourceCapture(
        capture_id=stable_id("capture", source, kind, payload_hash),
        source=source,
        kind=kind,
        station_id=config.station_id,
        requested_at=result.requested_at,
        received_at=result.received_at,
        local_date=result.received_at.astimezone(config.zone).date(),
        source_version=source_version or payload_hash,
        content_hash=payload_hash,
        request_url=result.request_url,
        http_status=result.status_code,
        content_type="application/json",
        raw_bytes=gzip.compress(result.raw_bytes, compresslevel=6),
        source_time=source_time,
        observed_at=observed_at,
        issued_at=issued_at,
    )


class WeatherCollector:
    def __init__(
        self,
        config: CityConfig,
        database_path: str,
        *,
        weather_client_factory: Callable[[], OfficialWeatherClient] = OfficialWeatherClient,
        page_client_factory: Callable[[], SettlementPageClient] = SettlementPageClient,
    ) -> None:
        self.config = config
        self.database_path = database_path
        self.weather_client_factory = weather_client_factory
        self.page_client_factory = page_client_factory

    def _save_metar(self, client: OfficialWeatherClient) -> bool:
        result = client.fetch_metar(self.config.station_id)
        observed_times = []
        for item in result.payload:
            reference = parse_time(item.get("receiptTime")) or result.received_at
            try:
                observed_times.append(
                    parse_metar_observed_at(str(item.get("rawOb", "")), reference)
                )
            except ValueError:
                continue
        latest = max(observed_times, default=None)
        capture = _json_capture(
            result,
            source="aviationweather",
            kind="metar",
            config=self.config,
            source_time=latest,
            observed_at=latest,
            source_version=str(latest.isoformat() if latest else content_hash(result.payload)),
        )
        observations = []
        parse_error_count = 0
        for item in result.payload:
            provider_received_at = parse_time(item.get("receiptTime"))
            report_time = parse_time(item.get("reportTime"))
            reference = provider_received_at or report_time or result.received_at
            try:
                observed_at = parse_metar_observed_at(str(item.get("rawOb", "")), reference)
            except ValueError:
                parse_error_count += 1
                continue
            temperature_c = item.get("temp")
            if observed_at is None or temperature_c is None:
                continue
            observations.append(
                {
                    "observed_at": observed_at,
                    "temperature_c": float(temperature_c),
                    "temperature_f": celsius_to_fahrenheit(float(temperature_c)),
                    "raw_unit": "degC",
                    "raw_text": str(item.get("rawOb", "")),
                    "quality_control": {},
                    "zone": self.config.zone,
                    "provider_received_at": provider_received_at,
                    "report_time": report_time,
                    "parser_version": "metar-ddhhmmz-v2",
                    "weather_metadata": {
                        "dewpoint_c": item.get("dewp"),
                        "wind_direction_deg": item.get("wdir"),
                        "wind_speed_kt": item.get("wspd"),
                        "wind_gust_kt": item.get("wgst"),
                        "visibility_sm": item.get("visib"),
                        "precip_in": item.get("precip"),
                        "cloud_layers": item.get("clouds") or [],
                    },
                }
            )
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            changed = store.save_source_capture(
                capture, payload=result.payload, observations=observations
            )
            store.record_poll_attempt(
                source=capture.source,
                kind=capture.kind,
                station_id=capture.station_id,
                requested_at=capture.requested_at,
                received_at=capture.received_at,
                http_status=capture.http_status,
                succeeded=True,
                content_changed=changed,
                capture_id=capture.capture_id if changed else None,
                content_hash_value=capture.content_hash,
                local_date=capture.local_date.isoformat(),
            )
            if parse_error_count:
                store.record_system_event(
                    capture.received_at,
                    "WARN",
                    "aviationweather",
                    "METAR_TIMESTAMP_PARSE_SKIPPED",
                    f"Skipped {parse_error_count} METAR rows with ambiguous DDHHMMZ",
                    {"capture_id": capture.capture_id, "collector": True},
                )
            return changed

    def _save_forecast(self, client: OfficialWeatherClient) -> bool:
        result = client.fetch_hourly_forecast(self.config)
        properties = result.payload.get("properties", {})
        issued_at = parse_time(properties.get("generatedAt"))
        periods = []
        for item in properties.get("periods", []):
            valid_at = parse_time(item.get("startTime"))
            if valid_at is None or item.get("temperature") is None:
                continue
            temperature = float(item["temperature"])
            unit = item.get("temperatureUnit")
            temperature_f = temperature if unit == "F" else celsius_to_fahrenheit(temperature)
            periods.append({"valid_at": valid_at, "temperature_f": temperature_f})
        capture = _json_capture(
            result,
            source="nws",
            kind="hourly_forecast",
            config=self.config,
            source_time=issued_at,
            issued_at=issued_at,
            source_version=str(properties.get("generatedAt") or content_hash(result.payload)),
        )
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            changed = store.save_source_capture(
                capture, payload=result.payload, forecast_periods=periods
            )
            store.record_poll_attempt(
                source=capture.source, kind=capture.kind, station_id=capture.station_id,
                requested_at=capture.requested_at, received_at=capture.received_at,
                http_status=capture.http_status, succeeded=True, content_changed=changed,
                capture_id=capture.capture_id if changed else None,
                content_hash_value=capture.content_hash,
                local_date=capture.local_date.isoformat(),
            )
            return changed

    def _save_nws_observations(self, client: OfficialWeatherClient) -> bool:
        result = client.fetch_nws_observations(self.config, utc_now())
        features = result.payload.get("features", [])
        observed_values = [
            parse_time(item.get("properties", {}).get("timestamp")) for item in features
        ]
        latest = max((value for value in observed_values if value is not None), default=None)
        capture = _json_capture(
            result,
            source="nws",
            kind="station_observations",
            config=self.config,
            source_time=latest,
            observed_at=latest,
            source_version=str(latest.isoformat() if latest else content_hash(result.payload)),
        )
        observations = []
        for feature in features:
            item = feature.get("properties", {})
            observed_at = parse_time(item.get("timestamp"))
            temperature = item.get("temperature") or {}
            value = temperature.get("value")
            if observed_at is None or value is None:
                continue
            unit = str(temperature.get("unitCode", ""))
            value = float(value)
            temperature_c = value if unit.endswith("degC") else (value - 32.0) * 5.0 / 9.0
            observations.append(
                {
                    "observed_at": observed_at,
                    "temperature_c": temperature_c,
                    "temperature_f": celsius_to_fahrenheit(temperature_c),
                    "raw_unit": unit,
                    "raw_text": str(item.get("rawMessage") or item.get("textDescription") or ""),
                    "quality_control": {
                        "temperature": temperature.get("qualityControl"),
                    },
                    "zone": self.config.zone,
                    "parser_version": "nws-observation-v2",
                    "weather_metadata": {
                        "dewpoint": item.get("dewpoint"),
                        "wind_direction": item.get("windDirection"),
                        "wind_speed": item.get("windSpeed"),
                        "wind_gust": item.get("windGust"),
                        "precipitation_last_hour": item.get("precipitationLastHour"),
                        "cloud_layers": item.get("cloudLayers") or [],
                    },
                }
            )
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            changed = store.save_source_capture(
                capture, payload=result.payload, observations=observations
            )
            store.record_poll_attempt(
                source=capture.source, kind=capture.kind, station_id=capture.station_id,
                requested_at=capture.requested_at, received_at=capture.received_at,
                http_status=capture.http_status, succeeded=True, content_changed=changed,
                capture_id=capture.capture_id if changed else None,
                content_hash_value=capture.content_hash,
                local_date=capture.local_date.isoformat(),
            )
            return changed

    def _save_settlement(self) -> bool:
        page_client = self.page_client_factory()
        page = page_client.fetch(self.config.collector.settlement_url)
        local_day = page.received_at.astimezone(self.config.zone).date()
        if page.received_at.astimezone(self.config.zone).time() <= datetime_time(1, 10):
            local_day -= timedelta(days=1)
        parsed = parse_settlement_page(page.text, local_day, self.config.zone)
        canonical = {
            "local_date": local_day.isoformat(),
            "table_text": parsed.table_text,
            "tmax_f": parsed.tmax_f,
            "finalized": parsed.finalized,
        }
        page_hash = content_hash(canonical)
        capture = SourceCapture(
            capture_id=stable_id("capture", "weather_gov", "settlement_page", page_hash),
            source="weather_gov",
            kind="settlement_page",
            station_id=self.config.station_id,
            requested_at=page.requested_at,
            received_at=page.received_at,
            local_date=local_day,
            source_version=page_hash,
            content_hash=page_hash,
            request_url=page.request_url,
            http_status=page.status_code,
            content_type="text/html",
            raw_bytes=gzip.compress(page.html.encode("utf-8"), compresslevel=6),
            source_time=parsed.page_updated_at,
        )
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            previous = store.latest_settlement_evidence(local_day)
            inserted = store.save_source_capture(capture, payload=canonical)
            if not inserted:
                store.record_poll_attempt(
                    source=capture.source,
                    kind=capture.kind,
                    station_id=capture.station_id,
                    requested_at=capture.requested_at,
                    received_at=capture.received_at,
                    http_status=capture.http_status,
                    succeeded=True,
                    content_changed=False,
                    content_hash_value=capture.content_hash,
                    local_date=capture.local_date.isoformat(),
                )
                return False
            previous_tmax = previous["tmax_f"] if previous is not None else None
            previous_finalized = bool(previous["finalized"]) if previous is not None else False
            screenshot_trigger = settlement_screenshot_trigger(
                parsed, previous_tmax, previous_finalized
            )
            screenshot_png = None
            if screenshot_trigger:
                screenshot_page = page_client.fetch(
                    self.config.collector.settlement_url, screenshot=True
                )
                screenshot_png = screenshot_page.screenshot_png
            evidence = SettlementEvidence(
                evidence_id=stable_id("settlement", capture.capture_id),
                capture_id=capture.capture_id,
                station_id=self.config.station_id,
                local_date=local_day,
                received_at=page.received_at,
                table_text=parsed.table_text,
                parse_status=parsed.parse_status,
                tmax_f=parsed.tmax_f,
                page_updated_at=parsed.page_updated_at,
                first_next_day_observed_at=parsed.first_next_day_observed_at,
                first_next_day_temperature_f=parsed.first_next_day_temperature_f,
                no_trade_reason=parsed.no_trade_reason,
                finalized=parsed.finalized,
                screenshot_png=screenshot_png,
                page_url=page.request_url,
                content_hash=page_hash,
                screenshot_trigger=screenshot_trigger,
                response_metadata={
                    "http_status": page.status_code,
                    "date": (page.response_headers or {}).get("date"),
                    "cache_control": (page.response_headers or {}).get("cache-control"),
                    "expires": (page.response_headers or {}).get("expires"),
                },
            )
            store.save_settlement_evidence(evidence, parsed.rows)
            store.record_poll_attempt(
                source=capture.source, kind=capture.kind, station_id=capture.station_id,
                requested_at=capture.requested_at, received_at=capture.received_at,
                http_status=capture.http_status, succeeded=True, content_changed=True,
                capture_id=capture.capture_id, content_hash_value=capture.content_hash,
                local_date=capture.local_date.isoformat(),
            )
        return True

    def _run_source(self, name: str, action: Callable[[], bool]) -> dict[str, Any]:
        requested_at = utc_now()
        try:
            changed = action()
            return {"source": name, "ok": True, "changed": changed}
        except Exception as exc:
            with WeatherStore(self.database_path) as store:
                store.init_schema()
                store.record_poll_attempt(
                    source=name,
                    kind={
                        "aviationweather": "metar",
                        "nws_forecast": "hourly_forecast",
                        "nws_observations": "station_observations",
                        "weather_gov": "settlement_page",
                    }.get(name, name),
                    station_id=self.config.station_id,
                    requested_at=requested_at,
                    received_at=utc_now(),
                    http_status=None,
                    succeeded=False,
                    content_changed=False,
                    error=exc,
                    local_date=requested_at.astimezone(self.config.zone).date().isoformat(),
                )
                store.record_system_event(
                    utc_now(),
                    "ERROR",
                    name,
                    type(exc).__name__,
                    str(exc),
                    {"collector": True},
                )
            return {
                "source": name,
                "ok": False,
                "changed": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
            }

    def collect_once(self, *, include_settlement: bool = True) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        with self.weather_client_factory() as client:
            results.append(self._run_source("aviationweather", lambda: self._save_metar(client)))
            results.append(self._run_source("nws_forecast", lambda: self._save_forecast(client)))
            results.append(
                self._run_source("nws_observations", lambda: self._save_nws_observations(client))
            )
        if include_settlement:
            results.append(self._run_source("weather_gov", self._save_settlement))
        return results

    def run_forever(self) -> None:
        tasks: dict[str, tuple[float, Callable[[], bool]]] = {}
        weather_client = self.weather_client_factory()
        try:
            tasks = {
                "aviationweather": (
                    float(self.config.collector.metar_interval_seconds),
                    lambda: self._save_metar(weather_client),
                ),
                "nws_forecast": (
                    float(self.config.collector.forecast_interval_seconds),
                    lambda: self._save_forecast(weather_client),
                ),
                "nws_observations": (
                    float(self.config.collector.nws_observation_interval_seconds),
                    lambda: self._save_nws_observations(weather_client),
                ),
            }
            next_due = {name: 0.0 for name in tasks}
            settlement_due = utc_now()
            while True:
                monotonic_now = time.monotonic()
                for name, (interval, action) in tasks.items():
                    if monotonic_now >= next_due[name]:
                        result = self._run_source(name, action)
                        print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                        next_due[name] = monotonic_now + interval
                now = utc_now()
                if now >= settlement_due:
                    result = self._run_source("weather_gov", self._save_settlement)
                    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
                    settlement_due = next_settlement_due(now, self.config)
                time.sleep(2)
        finally:
            weather_client.close()


def next_settlement_due(now: datetime, config: CityConfig) -> datetime:
    local_now = now.astimezone(config.zone)
    local_time = local_now.time()
    close_window = local_time >= datetime_time(23, 50) or local_time <= datetime_time(1, 10)
    if close_window:
        return now + timedelta(seconds=config.collector.settlement_close_interval_seconds)
    candidate = local_now.replace(minute=5, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(hours=1)
    close_start = local_now.replace(hour=23, minute=50, second=0, microsecond=0)
    if local_now < close_start < candidate:
        candidate = close_start
    return candidate.astimezone(UTC)

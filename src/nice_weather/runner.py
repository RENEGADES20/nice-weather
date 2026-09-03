from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from nice_weather.adapters.fixture import FixtureBundle, load_fixture
from nice_weather.adapters.polymarket import MarketDataRequestError, PolymarketReadOnlyAdapter
from nice_weather.config import CityConfig, load_city_config
from nice_weather.contract import parse_gamma_contract
from nice_weather.decision import build_outcomes
from nice_weather.domain import (
    BinProbability,
    DataHealth,
    Decision,
    ForecastPoint,
    HealthCheck,
    HealthLevel,
    MarketContract,
    OrderBook,
    PriceLevel,
    ProbabilityEstimate,
    RawSnapshot,
    RunMode,
    UnifiedState,
    WeatherObservation,
    content_hash,
    stable_id,
    utc_now,
)
from nice_weather.paper import PaperBroker
from nice_weather.probability import estimate_tmax
from nice_weather.reason_codes import ReasonCode
from nice_weather.store import WeatherStore
from nice_weather.weather_repository import WeatherRepository


def _age(decision_time: datetime, source_time: datetime) -> float:
    return max(0.0, (decision_time - source_time).total_seconds())


def _fixture_health(
    bundle: object, contract: object, config: CityConfig, *, require_books: bool = True
) -> DataHealth:
    decision_time = bundle.decision_time
    gamma = bundle.gamma_snapshot
    book_received = min((book.received_at for book in bundle.books.values()), default=None)
    book_source_time = min((book.exchange_time for book in bundle.books.values()), default=None)
    metar_observations = [
        item for item in bundle.observations if item.source == "aviationweather"
    ]
    nws_observations = [item for item in bundle.observations if item.source == "nws"]
    metar_received = max(
        (item.received_at for item in metar_observations), default=None
    )
    metar_source_time = max(
        (observation.observed_at for observation in metar_observations), default=None
    )
    forecast_received = bundle.forecasts[0].received_at if bundle.forecasts else None
    forecast_source_time = max((forecast.issued_at for forecast in bundle.forecasts), default=None)
    checks: list[HealthCheck] = []

    def check(
        source: str,
        received: datetime | None,
        source_time: datetime | None,
        receipt_limit: int,
        source_limit: int | None,
        missing: ReasonCode,
        stale_code: ReasonCode,
    ) -> None:
        if received is None:
            checks.append(HealthCheck(source, HealthLevel.BLOCKED, None, None, None, (missing,)))
            return
        receipt_age = _age(decision_time, received)
        source_age = _age(decision_time, source_time) if source_time else receipt_age
        stale = receipt_age > receipt_limit or (
            source_limit is not None and source_age > source_limit
        )
        checks.append(
            HealthCheck(
                source,
                HealthLevel.BLOCKED if stale else HealthLevel.OK,
                received,
                source_time,
                max(receipt_age, source_age),
                (stale_code,) if stale else (),
                message=(
                    f"receipt_age={receipt_age:.1f}s source_age={source_age:.1f}s "
                    f"limits={receipt_limit}/{source_limit or 'diagnostic-only'}s"
                ),
            )
        )

    check(
        "market_metadata",
        gamma.received_at,
        gamma.received_at,
        config.freshness.market_metadata_seconds,
        config.freshness.market_metadata_seconds,
        ReasonCode.DATA_MARKET_METADATA_MISSING,
        ReasonCode.DATA_MARKET_METADATA_STALE,
    )
    if require_books:
        check(
            "execution_quote",
            book_received,
            book_source_time,
            config.freshness.order_book_seconds,
            None,
            ReasonCode.DATA_ORDER_BOOK_MISSING,
            ReasonCode.DATA_ORDER_BOOK_STALE,
        )
    check(
        "metar",
        metar_received,
        metar_source_time,
        config.freshness.observation_receipt_seconds,
        config.freshness.observation_age_seconds,
        ReasonCode.DATA_OBSERVATION_MISSING,
        ReasonCode.DATA_OBSERVATION_STALE,
    )
    if bundle.manifest.get("live"):
        nws_received = max((item.received_at for item in nws_observations), default=None)
        nws_source_time = max(
            (item.observed_at for item in nws_observations), default=None
        )
        check(
            "nws_observations",
            nws_received,
            nws_source_time,
            config.freshness.observation_receipt_seconds,
            config.freshness.observation_age_seconds,
            ReasonCode.DATA_OBSERVATION_MISSING,
            ReasonCode.DATA_OBSERVATION_STALE,
        )
    check(
        "forecast",
        forecast_received,
        forecast_source_time,
        config.freshness.forecast_receipt_seconds,
        config.freshness.forecast_issue_seconds,
        ReasonCode.DATA_FORECAST_MISSING,
        ReasonCode.DATA_FORECAST_STALE,
    )
    forecast_hours = {
        item.valid_at.astimezone(UTC)
        for item in bundle.forecasts
        if item.valid_at.astimezone(config.zone).date() == contract.local_day
    }
    expected_hours = round(
        (contract.observation_end - contract.observation_start).total_seconds() / 3600
    )
    if len(forecast_hours) != expected_hours:
        checks.append(
            HealthCheck(
                "forecast_coverage",
                HealthLevel.BLOCKED,
                forecast_received,
                forecast_source_time,
                None,
                (ReasonCode.DATA_FORECAST_COVERAGE_GAP,),
                message=f"local_hours={len(forecast_hours)} expected={expected_hours}",
            )
        )
    if contract.ambiguities:
        checks.append(
            HealthCheck(
                "contract_rules",
                HealthLevel.BLOCKED,
                gamma.received_at,
                gamma.received_at,
                _age(decision_time, gamma.received_at),
                contract.ambiguities,
            )
        )
    level = (
        HealthLevel.BLOCKED
        if any(item.level is HealthLevel.BLOCKED for item in checks)
        else HealthLevel.OK
    )
    reasons = tuple(code for item in checks for code in item.reason_codes)
    return DataHealth(level, tuple(checks), tuple(dict.fromkeys(reasons)))


def run_fixture_once(
    manifest_path: str | Path,
    database_path: str | Path,
    config_path: str | Path | None = None,
) -> Decision:
    config = load_city_config(config_path)
    bundle = load_fixture(manifest_path, config)
    return _run_bundle(bundle, database_path, config, RunMode.FIXTURE)


def _run_bundle(
    bundle: FixtureBundle,
    database_path: str | Path,
    config: CityConfig,
    mode: RunMode,
    *,
    official_hourly_tmax_f: float | None = None,
    require_books: bool = True,
) -> Decision:
    contract = parse_gamma_contract(bundle.gamma_snapshot.payload, config)
    zone = config.zone
    observations = tuple(
        item
        for item in bundle.observations
        if item.observed_at.astimezone(zone).date() == contract.local_day
    )
    forecasts = tuple(
        item
        for item in bundle.forecasts
        if item.valid_at.astimezone(zone).date() == contract.local_day
    )
    health = _fixture_health(bundle, contract, config, require_books=require_books)
    input_ids = tuple(
        sorted(
            {
                *(snapshot.snapshot_id for snapshot in bundle.snapshots),
                *bundle.extra_input_snapshot_ids,
            }
        )
    )
    input_set_hash = content_hash(input_ids)
    state = UnifiedState(
        decision_time=bundle.decision_time,
        mode=mode,
        contract=contract,
        input_snapshot_ids=input_ids,
        order_books=bundle.books,
        observations=observations,
        forecasts=forecasts,
        health=health,
        input_set_hash=input_set_hash,
    )
    observed_tmax = official_hourly_tmax_f
    if forecasts:
        forecast_tmax = max(item.temperature_f for item in forecasts)
        estimate = estimate_tmax(
            contract,
            forecast_tmax,
            observed_tmax,
            config.model.sigma_f,
            bundle.decision_time,
            input_ids,
            config.model.version,
        )
    else:
        estimate = ProbabilityEstimate(
            model_version=config.model.version,
            generated_at=bundle.decision_time,
            baseline_tmax_f=0.0,
            observed_tmax_f=observed_tmax,
            mean_tmax_f=0.0,
            median_tmax_f=0.0,
            interval_low_f=0.0,
            interval_high_f=0.0,
            probabilities=tuple(BinProbability(item.bin_id, 0.0) for item in contract.bins),
            probability_sum=0.0,
            input_snapshot_ids=input_ids,
        )
    decision_id = stable_id(
        "decision",
        contract.contract_version_id,
        bundle.decision_time.isoformat(),
        input_set_hash,
        config.model.version,
    )
    if mode is RunMode.PAPER:
        with WeatherStore(database_path) as existing_store:
            existing_store.init_schema()
            broker = existing_store.load_paper_broker(config.paper.starting_cash)
        for open_order in broker.orders:
            broker.cancel(open_order, bundle.decision_time)
    else:
        broker = PaperBroker(config.paper.starting_cash)
    used_notional = sum(position.cost_basis for position in broker.positions.values())
    reserved_cash = sum(order.reserved_cash for order in broker.orders)
    outcomes = build_outcomes(
        decision_id,
        contract,
        estimate,
        bundle.books,
        config,
        health.level,
        float(broker.cash) - reserved_cash,
        used_notional,
        positions={bin_id: position.quantity for bin_id, position in broker.positions.items()},
    )
    approved = [item for item in outcomes if item.risk_approved]
    if approved and mode is not RunMode.SHADOW:
        selected = max(approved, key=lambda item: item.net_edge or float("-inf"))
        contract_bin = next(item for item in contract.bins if item.bin_id == selected.bin_id)
        broker.submit(
            selected,
            contract_bin,
            bundle.books[contract_bin.yes_token_id],
            bundle.decision_time,
            config.paper.stale_order_cycles,
        )
    positions = {bin_id: position.quantity for bin_id, position in broker.positions.items()}
    outcomes = tuple(
        replace(item, paper_position=positions.get(item.bin_id, 0.0)) for item in outcomes
    )
    reasons = health.reason_codes
    if not approved:
        reasons = tuple(
            dict.fromkeys(
                (*health.reason_codes,)
                + tuple(
                    code
                    for item in outcomes
                    if not item.risk_approved
                    for code in item.reason_codes
                )
            )
        )
    decision = Decision(
        decision_id=decision_id,
        decision_time=bundle.decision_time,
        mode=mode,
        contract_version_id=contract.contract_version_id,
        input_set_hash=input_set_hash,
        model_version=config.model.version,
        status="complete",
        overall_action="TRADE" if approved else "NO_TRADE",
        health_level=health.level,
        reason_codes=reasons,
        outcomes=outcomes,
    )
    account = broker.account_snapshot(contract.bins, bundle.books)
    with WeatherStore(database_path) as store:
        store.init_schema()
        store.save_run(
            bundle.snapshots,
            state,
            estimate,
            decision,
            broker,
            account,
            bundle.gamma_snapshot.snapshot_id,
        )
    return decision


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _book_from_snapshot(snapshot: RawSnapshot) -> OrderBook:
    payload = snapshot.payload
    exchange_time = datetime.fromtimestamp(int(payload["timestamp"]) / 1000, tz=UTC)
    return OrderBook(
        snapshot_id=snapshot.snapshot_id,
        book_hash=str(payload.get("hash", snapshot.source_version)),
        token_id=str(payload["asset_id"]),
        market_id=str(payload["market"]),
        exchange_time=exchange_time,
        received_at=snapshot.received_at,
        bids=tuple(
            sorted(
                (
                    PriceLevel(float(level["price"]), float(level["size"]))
                    for level in payload.get("bids", [])
                ),
                key=lambda level: level.price,
                reverse=True,
            )
        ),
        asks=tuple(
            sorted(
                (
                    PriceLevel(float(level["price"]), float(level["size"]))
                    for level in payload.get("asks", [])
                ),
                key=lambda level: level.price,
            )
        ),
    )


def _observations_from_snapshot(
    snapshot: RawSnapshot, station_id: str
) -> tuple[WeatherObservation, ...]:
    return tuple(
        WeatherObservation(
            snapshot_id=snapshot.snapshot_id,
            station_id=station_id,
            observed_at=_parse_time(item["reportTime"]),
            received_at=snapshot.received_at,
            temperature_f=float(item["temp"]) * 9.0 / 5.0 + 32.0,
            raw_text=str(item.get("rawOb", "")),
        )
        for item in snapshot.payload.get("observations", [])
        if item.get("temp") is not None
    )


def _forecasts_from_snapshot(snapshot: RawSnapshot) -> tuple[ForecastPoint, ...]:
    properties = snapshot.payload["properties"]
    issued_at = _parse_time(properties["generatedAt"])
    return tuple(
        ForecastPoint(
            snapshot_id=snapshot.snapshot_id,
            source="nws_hourly",
            issued_at=issued_at,
            valid_at=_parse_time(item["startTime"]),
            received_at=snapshot.received_at,
            temperature_f=float(item["temperature"]),
        )
        for item in properties.get("periods", [])
        if item.get("temperatureUnit") == "F"
    )


def _run_live_cycle(
    mode: RunMode,
    database_path: str | Path,
    config_path: str | Path | None = None,
) -> Decision:
    if mode not in (RunMode.SHADOW, RunMode.PAPER):
        raise ValueError("Live runner mode must be SHADOW or PAPER")
    config = load_city_config(config_path)
    request_time = utc_now()
    with PolymarketReadOnlyAdapter() as market_adapter:
        gamma_snapshot = market_adapter.discover(config, request_time)
        if gamma_snapshot.payload.get("selection_ambiguous"):
            return _persist_market_block(
                gamma_snapshot,
                database_path,
                config,
                mode,
                ReasonCode.MARKET_SELECTION_AMBIGUOUS,
            )
        if len(gamma_snapshot.payload.get("events", [])) != 1:
            return _persist_market_block(
                gamma_snapshot,
                database_path,
                config,
                mode,
                ReasonCode.MARKET_NOT_FOUND,
            )
        contract = parse_gamma_contract(gamma_snapshot.payload, config)
        weather_as_of = utc_now()
        weather_state = WeatherRepository(database_path).get_state_as_of(
            config.station_id, contract.local_day, weather_as_of
        )
        forecasts = tuple(
            item
            for item in weather_state.forecasts
            if item.valid_at.astimezone(config.zone).date() == contract.local_day
        )
        official_tmax = (
            float(weather_state.settlement["tmax_f"])
            if weather_state.settlement and weather_state.settlement.get("tmax_f") is not None
            else None
        )
        candidate_tokens: list[str] = []
        if forecasts:
            preliminary = estimate_tmax(
                contract,
                max(item.temperature_f for item in forecasts),
                official_tmax,
                config.model.sigma_f,
                weather_as_of,
                weather_state.input_capture_ids,
                config.model.version,
            )
            probability_by_bin = {
                item.bin_id: item.probability for item in preliminary.probabilities
            }
            candidate_tokens = [
                item.yes_token_id
                for item in contract.bins
                if item.active
                and not item.closed
                and probability_by_bin.get(item.bin_id, 0.0)
                >= config.signal.quote_probability_floor
            ]
            if not candidate_tokens:
                candidate_tokens = [
                    item.yes_token_id
                    for item in sorted(
                        contract.bins,
                        key=lambda item: probability_by_bin.get(item.bin_id, 0.0),
                        reverse=True,
                    )[:3]
                ]
        if mode is RunMode.PAPER:
            with WeatherStore(database_path) as store:
                store.init_schema()
                broker = store.load_paper_broker(config.paper.starting_cash)
            by_bin = {item.bin_id: item for item in contract.bins}
            for bin_id, position in broker.positions.items():
                if position.quantity and bin_id in by_bin:
                    candidate_tokens.append(by_bin[bin_id].yes_token_id)
        candidate_tokens = list(dict.fromkeys(candidate_tokens))
        book_snapshots = market_adapter.fetch_candidate_quotes(candidate_tokens, weather_as_of)
    decision_time = utc_now()
    books = {
        book.token_id: book
        for book in (_book_from_snapshot(snapshot) for snapshot in book_snapshots)
    }
    observations = weather_state.observations
    snapshots = (gamma_snapshot, *book_snapshots)
    if any(snapshot.received_at > decision_time for snapshot in snapshots):
        raise RuntimeError(ReasonCode.DATA_AS_OF_VIOLATION.value)
    bundle = FixtureBundle(
        manifest={"live": True},
        decision_time=decision_time,
        snapshots=snapshots,
        gamma_snapshot=gamma_snapshot,
        books=books,
        observations=observations,
        forecasts=forecasts,
        extra_input_snapshot_ids=weather_state.input_capture_ids,
    )
    return _run_bundle(
        bundle,
        database_path,
        config,
        mode,
        official_hourly_tmax_f=official_tmax,
        require_books=bool(candidate_tokens),
    )


def run_live_once(
    mode: RunMode,
    database_path: str | Path,
    config_path: str | Path | None = None,
) -> Decision:
    config = load_city_config(config_path)
    owner_id = stable_id("runner", os.getpid(), Path(database_path).resolve())
    lock_name = "nice-weather-writer"
    acquired_at = utc_now()
    with WeatherStore(database_path) as store:
        store.init_schema()
        acquired = store.acquire_runner_lock(
            lock_name,
            owner_id,
            acquired_at,
            config.freshness.runner_heartbeat_seconds * 2,
        )
    if not acquired:
        raise RuntimeError(ReasonCode.SYSTEM_RUNNER_LOCKED.value)
    try:
        return _run_live_cycle(mode, database_path, config_path)
    except Exception as exc:
        context = {"mode": mode.value}
        if isinstance(exc, MarketDataRequestError):
            context.update(exc.context)
        with WeatherStore(database_path) as store:
            store.init_schema()
            store.record_system_event(
                utc_now(),
                "ERROR",
                "runner",
                type(exc).__name__,
                str(exc),
                context,
            )
        raise
    finally:
        with WeatherStore(database_path) as store:
            store.init_schema()
            store.release_runner_lock(lock_name, owner_id, utc_now())


def _persist_market_block(
    snapshot: RawSnapshot,
    database_path: str | Path,
    config: CityConfig,
    mode: RunMode,
    reason: ReasonCode,
) -> Decision:
    decision_time = utc_now()
    local_day = decision_time.astimezone(config.zone).date()
    local_start = datetime.combine(local_day, time.min, tzinfo=config.zone)
    contract = MarketContract(
        contract_version_id=stable_id("contract", reason.value, local_day),
        event_id="unavailable",
        event_slug="unavailable",
        event_title="NYC/KLGA market unavailable",
        market_url="",
        local_day=local_day,
        city_code=config.city_code,
        station_id=config.station_id,
        timezone=config.timezone,
        metric=config.metric,
        unit="F",
        rounding="unavailable",
        observation_start=local_start.astimezone(UTC),
        observation_end=(local_start + timedelta(days=1)).astimezone(UTC),
        settlement_source="",
        rule_text="",
        rule_version="unavailable",
        rule_hash=content_hash(snapshot.payload),
        parse_status="blocked",
        ambiguities=(reason,),
        event_active=False,
        event_closed=False,
        bins=(),
    )
    health = DataHealth(
        HealthLevel.BLOCKED,
        (
            HealthCheck(
                "market_metadata",
                HealthLevel.BLOCKED,
                snapshot.received_at,
                snapshot.received_at,
                _age(decision_time, snapshot.received_at),
                (reason,),
            ),
        ),
        (reason,),
    )
    input_ids = (snapshot.snapshot_id,)
    input_hash = content_hash(input_ids)
    state = UnifiedState(
        decision_time=decision_time,
        mode=mode,
        contract=contract,
        input_snapshot_ids=input_ids,
        order_books={},
        observations=(),
        forecasts=(),
        health=health,
        input_set_hash=input_hash,
    )
    estimate = ProbabilityEstimate(
        config.model.version,
        decision_time,
        0.0,
        None,
        0.0,
        0.0,
        0.0,
        0.0,
        (),
        0.0,
        input_ids,
    )
    decision = Decision(
        decision_id=stable_id("decision", contract.contract_version_id, decision_time, input_hash),
        decision_time=decision_time,
        mode=mode,
        contract_version_id=contract.contract_version_id,
        input_set_hash=input_hash,
        model_version=config.model.version,
        status="complete",
        overall_action="NO_TRADE",
        health_level=HealthLevel.BLOCKED,
        reason_codes=(reason,),
        outcomes=(),
    )
    broker = PaperBroker(config.paper.starting_cash)
    account = broker.account_snapshot((), {})
    with WeatherStore(database_path) as store:
        store.init_schema()
        store.save_run(
            (snapshot,), state, estimate, decision, broker, account, snapshot.snapshot_id
        )
    return decision

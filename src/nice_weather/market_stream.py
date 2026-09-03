from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from nice_weather.adapters.polymarket import PolymarketReadOnlyAdapter
from nice_weather.config import CityConfig
from nice_weather.contract import parse_gamma_contract
from nice_weather.domain import MarketTopTick, content_hash, stable_id, utc_now
from nice_weather.store import WeatherStore

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_time(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        timestamp = float(value)
        if timestamp > 100_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).astimezone(UTC)
        except ValueError:
            pass
    return fallback


@dataclass(frozen=True)
class TokenMetadata:
    event_id: str
    condition_id: str
    market_id: str
    bin_id: str
    token_id: str
    label: str


class MarketStreamCollector:
    def __init__(
        self,
        config: CityConfig,
        database_path: str,
        *,
        adapter_factory: type[PolymarketReadOnlyAdapter] = PolymarketReadOnlyAdapter,
        websocket_url: str = WS_URL,
    ) -> None:
        self.config = config
        self.database_path = database_path
        self.adapter_factory = adapter_factory
        self.websocket_url = websocket_url
        self.state: dict[str, dict[str, float | None]] = {}

    def _save(
        self,
        metadata: TokenMetadata,
        *,
        exchange_event_at: datetime,
        received_at: datetime,
        source: str,
        status: str,
        changes: dict[str, float | None],
        raw_event: Any,
    ) -> bool:
        state = self.state.setdefault(
            metadata.token_id,
            {
                "best_bid": None,
                "best_ask": None,
                "bid_size": None,
                "ask_size": None,
                "last_trade_price": None,
            },
        )
        before = tuple(state.values())
        state.update(changes)
        if tuple(state.values()) == before and status == "available":
            return False
        best_bid = state["best_bid"]
        best_ask = state["best_ask"]
        mid = None
        normalized_status = status
        if best_bid is not None and best_ask is not None:
            if best_bid <= best_ask:
                mid = (best_bid + best_ask) / 2
            else:
                normalized_status = "crossed"
        event_hash = content_hash(raw_event)
        tick = MarketTopTick(
            tick_id=stable_id(
                "market_tick", source, metadata.token_id, exchange_event_at, event_hash
            ),
            event_id=metadata.event_id,
            condition_id=metadata.condition_id,
            market_id=metadata.market_id,
            bin_id=metadata.bin_id,
            token_id=metadata.token_id,
            label=metadata.label,
            exchange_event_at=exchange_event_at,
            received_at=received_at,
            object_timezone=self.config.timezone,
            object_local_date=exchange_event_at.astimezone(self.config.zone).date(),
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=state["bid_size"],
            ask_size=state["ask_size"],
            mid=mid,
            last_trade_price=state["last_trade_price"],
            source=source,
            status=normalized_status,
            event_hash=event_hash,
        )
        with WeatherStore(self.database_path) as store:
            store.init_schema()
            return store.save_market_top_tick(tick)

    def discover(self) -> tuple[dict[str, TokenMetadata], dict[str, Any]]:
        now = utc_now()
        with self.adapter_factory() as adapter:
            snapshot = adapter.discover(self.config, now)
        contract = parse_gamma_contract(snapshot.payload, self.config)
        metadata = {
            item.yes_token_id: TokenMetadata(
                event_id=contract.event_id,
                condition_id=item.condition_id,
                market_id=item.market_id,
                bin_id=item.bin_id,
                token_id=item.yes_token_id,
                label=item.label,
            )
            for item in contract.bins
        }
        event = snapshot.payload["events"][0]
        markets = {str(item.get("id")): item for item in event.get("markets", [])}
        for item in contract.bins:
            market = markets.get(item.market_id, {})
            bid = _number(market.get("bestBid"))
            ask = _number(market.get("bestAsk"))
            self._save(
                metadata[item.yes_token_id],
                exchange_event_at=_event_time(market.get("updatedAt"), snapshot.received_at),
                received_at=snapshot.received_at,
                source="gamma_fallback",
                status="available" if bid is not None or ask is not None else "missing",
                changes={
                    "best_bid": bid,
                    "best_ask": ask,
                    "last_trade_price": _number(market.get("lastTradePrice")),
                },
                raw_event={
                    "bestBid": market.get("bestBid"),
                    "bestAsk": market.get("bestAsk"),
                    "lastTradePrice": market.get("lastTradePrice"),
                    "updatedAt": market.get("updatedAt"),
                },
            )
        try:
            with self.adapter_factory() as adapter:
                snapshots = adapter.fetch_books_batch_payload(list(metadata))
            snapshot_time = utc_now()
            for book in snapshots:
                token_id = str(book.get("asset_id") or book.get("token_id") or "")
                target = metadata.get(token_id)
                if target is None:
                    continue
                self._save(
                    target,
                    exchange_event_at=_event_time(book.get("timestamp"), snapshot_time),
                    received_at=snapshot_time,
                    source="clob_ws",
                    status="reconnect_snapshot",
                    changes=self._book_changes(book),
                    raw_event={"reconnect_snapshot": book},
                )
        except (OSError, RuntimeError, ValueError):
            pass
        return metadata, snapshot.payload

    def _book_changes(self, event: dict[str, Any]) -> dict[str, float | None]:
        bids = [item for item in event.get("bids", []) if _number(item.get("price")) is not None]
        asks = [item for item in event.get("asks", []) if _number(item.get("price")) is not None]
        best_bid = max(bids, key=lambda item: float(item["price"])) if bids else None
        best_ask = min(asks, key=lambda item: float(item["price"])) if asks else None
        return {
            "best_bid": _number(best_bid.get("price")) if best_bid else None,
            "best_ask": _number(best_ask.get("price")) if best_ask else None,
            "bid_size": _number(best_bid.get("size")) if best_bid else None,
            "ask_size": _number(best_ask.get("size")) if best_ask else None,
        }

    def process_message(
        self, message: str | bytes, metadata: dict[str, TokenMetadata]
    ) -> int:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        if not message.strip() or message.strip().upper() == "PONG":
            return 0
        decoded = json.loads(message)
        events = decoded if isinstance(decoded, list) else [decoded]
        saved = 0
        for event in events:
            if not isinstance(event, dict):
                continue
            received_at = utc_now()
            event_type = str(event.get("event_type", ""))
            if event_type == "price_change":
                changes = event.get("price_changes", [])
            else:
                changes = [event]
            for change in changes:
                token_id = str(change.get("asset_id") or event.get("asset_id") or "")
                target = metadata.get(token_id)
                if target is None:
                    continue
                values: dict[str, float | None]
                if event_type == "book":
                    values = self._book_changes(event)
                elif event_type == "last_trade_price":
                    values = {"last_trade_price": _number(event.get("price"))}
                else:
                    values = {}
                    side = str(change.get("side", "")).upper()
                    changed_price = _number(change.get("price"))
                    changed_size = _number(change.get("size"))
                    for key, expected_side in (("best_bid", "BUY"), ("best_ask", "SELL")):
                        if key not in change:
                            continue
                        best_price = _number(change.get(key))
                        values[key] = best_price
                        size_key = "bid_size" if key == "best_bid" else "ask_size"
                        values[size_key] = (
                            changed_size
                            if side == expected_side and changed_price == best_price
                            else None
                        )
                exchange_time = _event_time(event.get("timestamp"), received_at)
                saved += int(
                    self._save(
                        target,
                        exchange_event_at=exchange_time,
                        received_at=received_at,
                        source="clob_ws",
                        status="available",
                        changes=values,
                        raw_event=event,
                    )
                )
        return saved

    def run_forever(self) -> None:
        reconnect = self.config.collector.market_stream_reconnect_seconds
        metadata: dict[str, TokenMetadata] = {}
        while True:
            try:
                metadata, _ = self.discover()
                if not metadata:
                    time.sleep(reconnect)
                    continue
                with connect(self.websocket_url, open_timeout=15, close_timeout=5) as websocket:
                    websocket.send(
                        json.dumps(
                            {
                                "assets_ids": list(metadata),
                                "type": "market",
                                "custom_feature_enabled": True,
                            }
                        )
                    )
                    deadline = time.monotonic() + (
                        self.config.collector.market_discovery_interval_seconds
                    )
                    next_ping = time.monotonic() + 10
                    while time.monotonic() < deadline:
                        remaining_to_ping = max(0.1, next_ping - time.monotonic())
                        try:
                            message = websocket.recv(timeout=remaining_to_ping)
                        except TimeoutError:
                            message = None
                        if time.monotonic() >= next_ping:
                            websocket.send("PING")
                            next_ping = time.monotonic() + 10
                        if message is not None:
                            self.process_message(message, metadata)
            except (ConnectionClosed, OSError, ValueError, json.JSONDecodeError) as exc:
                disconnected_at = utc_now()
                for target in metadata.values():
                    self._save(
                        target,
                        exchange_event_at=disconnected_at,
                        received_at=disconnected_at,
                        source="clob_ws",
                        status="disconnect",
                        changes={},
                        raw_event={"disconnect": type(exc).__name__},
                    )
                print(
                    json.dumps(
                        {
                            "status": "reconnecting",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                time.sleep(reconnect)

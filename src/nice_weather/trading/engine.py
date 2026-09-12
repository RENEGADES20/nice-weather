"""Native Nautilus execution; SQLite holds inputs and views, never a second fill ledger."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from importlib.metadata import version

from nice_weather.trading import ENGINE_VERSION, validate_strategy
from nice_weather.trading.dataset import timestamp, valid_quote
from nice_weather.trading.storage import digest

if sys.version_info[:2] != (3, 12) or version("nautilus_trader") != ENGINE_VERSION:
    raise RuntimeError("Trading requires isolated Python 3.12 / nautilus_trader==1.231.0")

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FeeModel, FillModel
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig, StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.currencies import pUSD
from nautilus_trader.model.data import CustomData, DataType, InstrumentClose, QuoteTick
from nautilus_trader.model.enums import (
    AccountType,
    AssetClass,
    BookType,
    InstrumentCloseType,
    LiquiditySide,
    OmsType,
    OrderSide,
    TimeInForce,
)
from nautilus_trader.model.identifiers import ClientId, ClientOrderId, InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.trading.strategy import Strategy

VENUE = Venue("POLYMARKET")


class Input(Data):
    def __init__(self, event):
        self.event = event

    @property
    def ts_init(self):
        return self.event["ts"]

    @property
    def ts_event(self):
        return self.event["ts"]


class Fees(FeeModel):
    def __init__(self, session):
        self.session = session

    def get_commission(self, order, fill_qty, fill_px, instrument):
        if str(order.client_order_id).startswith("EXPIRATION-"):
            return Money(0, pUSD)
        row = self.session.metadata[instrument.raw_symbol.value]
        if order.liquidity_side == LiquiditySide.MAKER:
            return Money(0, pUSD)  # No assumed maker rebates.
        p, q = fill_px.as_decimal(), fill_qty.as_decimal()
        fee = q * Decimal(str(row["fee_rate"])) * (p * (1 - p)) ** Decimal(str(row["fee_exponent"]))
        return Money(fee, pUSD)


class Control(Strategy):
    def __init__(self, session):
        super().__init__(
            StrategyConfig(strategy_id="Workbench-001", order_id_tag="001", manage_gtd_expiry=True)
        )
        self.session = session
        self.replacement = None

    def on_order_canceled(self, event):
        if self.replacement and self.replacement[1] == str(event.client_order_id):
            command, _ = self.replacement
            self.replacement = None
            self.session.command(command)

    def on_start(self):
        self.subscribe_data(DataType(Input), client_id=ClientId("WORKBENCH"))

    def on_data(self, data):
        if isinstance(data, Input):
            self.session.command(data.event)


def number(value, name, *, positive=True):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {name}")
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"Invalid {name}") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"Invalid {name}")
    return result


class Session:
    def __init__(self, config: dict):
        if config["mode"] not in {"sandbox", "backtest"}:
            raise ValueError("Simulation cannot impersonate a live account")
        self.config = config
        self.projection_version = config.get("projection_version", 1)
        self.parameters = validate_strategy(
            config["strategy_id"], config.get("parameters", {}), config["mode"]
        )
        self.cash_start = float(number(config.get("cash", 100), "cash"))
        if self.cash_start > 1_000_000:
            raise ValueError("Initial cash exceeds supported limit")
        self.metadata, self.instruments, self.quotes = {}, {}, {}
        # Nautilus copies an empty settlement mapping; a sentinel keeps this mapping shared.
        self.settlement_prices = {InstrumentId.from_str("UNUSED.POLYMARKET"): 0.0}
        self.outcomes, self.owner = {}, {}
        self.counts, self.rejections = {}, []
        self.equity_peak, self.maximum_drawdown = self.cash_start, 0.0
        self.now = 0
        self.enabled = False
        self.engine = BacktestEngine(
            BacktestEngineConfig(
                trader_id="WEATHER-001", logging=LoggingConfig(bypass_logging=True)
            )
        )
        self.engine.add_venue(
            venue=VENUE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=pUSD,
            starting_balances=[Money(self.cash_start, pUSD)],
            book_type=BookType.L1_MBP,
            liquidity_consumption=True,
            fill_model=FillModel(prob_fill_on_limit=0, random_seed=7),
            fee_model=Fees(self),
            settlement_prices=self.settlement_prices,
        )
        self.control = Control(self)
        self.engine.add_strategy(self.control)

    def _run(self, data, *, custom=False):
        self.engine.add_data([data], client_id=ClientId("WORKBENCH") if custom else None)
        self.engine.run(streaming=True)
        self.engine.clear_data()

    def apply(self, event: dict):
        if event["ts"] < self.now:
            raise ValueError("Nonmonotonic received-time input")
        self.now = event["ts"]
        kind, row = event["kind"], event.get("data", {})
        # Cancel stale resting orders before a new book can match them.
        cancel_pending = False
        for token, quote in list(self.quotes.items()):
            if self.now - quote["ts"] > 30_000_000_000 or self.market_rejection(token):
                cancel_pending |= self.cancel_token(token)
        if cancel_pending:
            # Native cancellation is queued; drain it before introducing a crossing quote.
            self._run(CustomData(DataType(Input), Input(event | {"kind": "clock"})), custom=True)
        if kind == "contract":
            for side, key in (("YES", "yes_token_id"), ("NO", "no_token_id")):
                token = row[key]
                if not token:
                    continue
                self.metadata[token] = row | {"outcome": side}
                if self.market_rejection(token):
                    self.cancel_token(token)
                previous = self.instruments.get(token)
                increment = Price.from_str(str(row["tick_size"]))
                if previous is None or increment != previous.price_increment:
                    if previous is not None:
                        self.cancel_token(token)
                        self._run(
                            CustomData(DataType(Input), Input(event | {"kind": "clock"})),
                            custom=True,
                        )
                        self.quotes.pop(token, None)
                    instrument = BinaryOption(
                        instrument_id=InstrumentId.from_str(
                            f"{row['condition_id']}-{token}.POLYMARKET"
                        ),
                        raw_symbol=Symbol(token),
                        asset_class=AssetClass.ALTERNATIVE,
                        currency=pUSD,
                        price_precision=increment.precision,
                        price_increment=increment,
                        size_precision=6,
                        size_increment=Quantity.from_str("0.000001"),
                        activation_ns=0,
                        expiration_ns=4102444800000000000,
                        ts_event=self.now,
                        ts_init=self.now,
                        outcome=side,
                        description=row["label"],
                    )
                    # Expiry stays deferred until a verified result is actually received.
                    self.instruments[token] = instrument
                    if previous is None:
                        self.engine.add_instrument(instrument)
                    else:
                        self._run(instrument, custom=True)
            self._run(CustomData(DataType(Input), Input(event | {"kind": "clock"})), custom=True)
        elif kind == "quote":
            token = row["token_id"]
            if token not in self.instruments:
                return
            if not valid_quote(row):
                self.quotes.pop(token, None)
                self.cancel_token(token)
            else:
                ins = self.instruments[token]
                self.quotes[token] = row | {
                    "ts": timestamp(row["received_at"]) if row.get("received_at") else self.now
                }
                self._run(
                    QuoteTick(
                        ins.id,
                        ins.make_price(row["best_bid"]),
                        ins.make_price(row["best_ask"]),
                        ins.make_qty(row["bid_size"]),
                        ins.make_qty(row["ask_size"]),
                        self.now,
                        self.now,
                    )
                )
                self._run(
                    CustomData(
                        DataType(Input), Input(event | {"kind": "strategy_tick", "token": token})
                    ),
                    custom=True,
                )
        elif kind == "settlement":
            if row.get("evidence_type") != "official_final" or not row.get("source_hash"):
                raise ValueError("Settlement requires verifiable final source evidence")
            token, value = row["token_id"], row["value"]
            raw = row.get("source_payload", {})
            definition = self.metadata[token]
            expected = {definition["yes_token_id"], definition["no_token_id"]}
            outcomes = {str(t.get("token_id")): t.get("winner") for t in raw.get("tokens", [])}
            if (
                digest(raw) != row["source_hash"]
                or raw.get("closed") is not True
                or raw.get("condition_id") != definition["condition_id"]
                or set(outcomes) != expected
                or sum(v is True for v in outcomes.values()) != 1
                or value != int(outcomes[token] is True)
            ):
                raise ValueError("Final outcome evidence does not match contract / content hash")
            if type(value) is not int or value not in (0, 1) or token in self.outcomes:
                raise ValueError("Invalid or duplicate settlement")
            ins = self.instruments[token]
            self.outcomes[token] = value
            self.settlement_prices[ins.id] = float(value)
            self._run(
                InstrumentClose(
                    ins.id,
                    ins.make_price(value),
                    InstrumentCloseType.CONTRACT_EXPIRED,
                    self.now,
                    self.now,
                )
            )
        else:
            self._run(CustomData(DataType(Input), Input(event)), custom=True)
        snapshot = self.snapshot()
        if snapshot["equity"] is not None:
            self.equity_peak = max(self.equity_peak, snapshot["equity"])
            self.maximum_drawdown = snapshot["max_drawdown"]
        return snapshot

    def open_orders(self):
        return self.engine.cache.orders_open()

    def market_rejection(self, token):
        row = self.metadata[token]
        if (
            row["parse_status"] != "parsed"
            or json_nonempty(row["ambiguities_json"])
            or row["station_id"] != "KLGA"
            or row["timezone"] != "America/New_York"
        ):
            return "Ambiguous contract: no-trade"
        if not row.get("fee_known", False):
            return "Unknown historical fee schedule: no-trade"
        if (
            row["closed"]
            or not row["active"]
            or not row["accepting_orders"]
            or self.now >= timestamp(row["observation_end"])
            or token in self.outcomes
        ):
            return "Market closed"
        return None

    def buy_reserve(self, order):
        token = self.engine.cache.instrument(order.instrument_id).raw_symbol.value
        row = self.metadata[token]
        fee_cap = float(row["fee_rate"]) * 0.25 ** float(row["fee_exponent"])
        return float(order.leaves_qty) * (float(order.price) + fee_cap)

    def cancel_token(self, token):
        ins = self.instruments.get(token)
        requested = False
        if ins:
            for order in self.open_orders():
                if order.instrument_id == ins.id:
                    self.control.cancel_order(order)
                    requested = True
        return requested

    def command(self, event):
        kind, payload = event["kind"], event.get("data", {})
        request_id = event.get("request_id", f"auto-{self.now}-{event.get('token', '')}")
        try:
            if kind == "projection_upgrade":
                if payload.get("version") != 2:
                    raise ValueError("Unsupported projection version")
                self.projection_version = 2
                return
            if kind == "clock":
                return
            if kind == "start":
                if "strategy_id" in payload:
                    self.parameters = validate_strategy(
                        payload["strategy_id"], payload.get("parameters", {}), self.config["mode"]
                    )
                    self.config = self.config | {
                        "strategy_id": payload["strategy_id"],
                        "tokens": payload.get("tokens", []),
                    }
                self.enabled = True
                return
            if kind == "stop":
                self.enabled = False
                for order in self.open_orders():
                    if self.owner.get(str(order.client_order_id)) == "strategy":
                        self.control.cancel_order(order)
                return
            if kind == "strategy_tick":
                token = event["token"]
                if not self.enabled or self.config["strategy_id"] == "noop":
                    return
                if token not in self.config.get("tokens", []):
                    return
                if self.parameters.get("require_both"):
                    definition = self.metadata[token]
                    for key in ("yes_token_id", "no_token_id"):
                        other = self.quotes.get(definition[key])
                        if not other or not 0 <= self.now - other["ts"] <= 30_000_000_000:
                            return
                self.counts[token] = self.counts.get(token, 0) + 1
                if self.counts[token] == 1:
                    self.order(
                        request_id,
                        {
                            "token": token,
                            "side": "BUY",
                            "tif": "IOC",
                            "quantity": self.parameters["quantity"],
                            "price": self.quotes[token]["best_ask"],
                        },
                        "strategy",
                    )
                elif self.counts[token] == self.parameters["exit_after_quotes"]:
                    self.exit(request_id, {"token": token}, "strategy")
                return
            if kind in {"cancel", "replace"}:
                old = self.engine.cache.order(ClientOrderId(payload["order_id"]))
                if old is None:
                    raise ValueError("Unknown order; replacement blocked")
                if kind == "replace" and (
                    payload["token"] not in self.instruments
                    or self.instruments[payload["token"]].id != old.instrument_id
                    or payload["side"] != old.side.name
                ):
                    raise ValueError("Replacement must retain token and side")
                if kind == "replace" and not old.is_closed:
                    self.control.replacement = (event, str(old.client_order_id))
                if not old.is_closed:
                    self.control.cancel_order(old)
                if kind == "replace":
                    # The synchronous simulator confirms cancellation before returning.
                    # An asynchronous/unknown result must never submit the replacement.
                    if old.is_pending_cancel:
                        return
                    if old.status.name != "CANCELED":
                        raise ValueError("Cancellation unconfirmed; replacement blocked")
                    quantity = (
                        number(payload["target_quantity"], "target") - old.filled_qty.as_decimal()
                    )
                    if quantity > 0:
                        self.order(
                            request_id,
                            payload | {"quantity": str(quantity)},
                            self.owner.get(str(old.client_order_id), "manual"),
                        )
                return
            if kind == "close":
                self.exit(request_id, payload, "manual")
            elif kind == "order":
                self.order(request_id, payload, "manual")
            else:
                raise ValueError("Unsupported engine command")
        except (ValueError, KeyError) as exc:
            self.rejections.append({"request_id": request_id, "ts": self.now, "reason": str(exc)})

    def position_quantity(self, token):
        return sum(
            (
                p.quantity.as_decimal()
                for p in self.engine.cache.positions_open()
                if p.instrument_id == self.instruments[token].id
            ),
            Decimal(0),
        )

    def exit(self, request_id, payload, owner):
        token = payload["token"]
        if token not in self.instruments or token not in self.quotes:
            raise ValueError("No executable exit quote")
        quantity = payload.get("quantity", str(self.position_quantity(token)))
        self.order(
            request_id,
            {
                "token": token,
                "quantity": quantity,
                "side": "SELL",
                "price": payload.get("price", self.quotes[token]["best_bid"]),
                "tif": "IOC",
            },
            owner,
        )

    def order(self, request_id, payload, owner):
        if self.engine.cache.order(ClientOrderId(request_id)) is not None:
            return
        token = payload["token"]
        if token not in self.metadata or token not in self.quotes:
            raise ValueError("Missing contract or executable quote")
        row, quote, ins = self.metadata[token], self.quotes[token], self.instruments[token]
        if reason := self.market_rejection(token):
            raise ValueError(reason)
        if self.now - quote["ts"] > 30_000_000_000:
            raise ValueError("Stale quote")
        side, tif = payload.get("side"), payload.get("tif", "GTC")
        if side not in {"BUY", "SELL"} or tif not in {"GTC", "GTD", "IOC", "FOK"}:
            raise ValueError("Unsupported side or time in force")
        price = number(payload["price"], "price")
        if price >= 1 or price % Decimal(str(row["tick_size"])):
            raise ValueError("Illegal price / tick precision")
        if "amount" in payload:
            if side != "BUY" or "quantity" in payload:
                raise ValueError("Use either buy amount or share quantity")
            quantity = (number(payload["amount"], "amount") / price).quantize(
                Decimal("0.000001"), rounding=ROUND_DOWN
            )
        else:
            quantity = number(payload["quantity"], "quantity")
        if quantity % Decimal("0.000001") or quantity < Decimal(str(row["minimum_order_size"])):
            raise ValueError("Illegal quantity precision or below market minimum")
        post_only = payload.get("post_only", False)
        if type(post_only) is not bool or (post_only and tif not in {"GTC", "GTD"}):
            raise ValueError("Illegal post-only combination")
        crosses = (
            price >= Decimal(str(quote["best_ask"]))
            if side == "BUY"
            else price <= Decimal(str(quote["best_bid"]))
        )
        if post_only and crosses:
            raise ValueError("Post-only would take liquidity")
        expire = None
        if tif == "GTD":
            expire_ns = timestamp(payload["expire_time"])
            if expire_ns <= self.now:
                raise ValueError("Order already expired")
            expire = datetime.fromtimestamp(expire_ns / 1e9, UTC)
        existing = self.open_orders()
        if side == "SELL":
            reserved = sum(
                (
                    o.leaves_qty.as_decimal()
                    for o in existing
                    if o.instrument_id == ins.id and o.side == OrderSide.SELL
                ),
                Decimal(0),
            )
            if quantity > self.position_quantity(token) - reserved:
                raise ValueError("Naked sell / shares already reserved")
        else:
            cost = float(price * quantity)
            account = self.engine.cache.account_for_venue(VENUE)
            # Max fee over the legal binary price interval; conservative for resting orders.
            fee = float(quantity) * float(row["fee_rate"]) * 0.25 ** float(row["fee_exponent"])
            reserved = sum(self.buy_reserve(o) for o in existing if o.side == OrderSide.BUY)
            cash = float(account.balance_total(pUSD)) if account else self.cash_start
            if cost + fee + reserved > cash + 1e-9:
                raise ValueError("Insufficient cash including open orders")
            bin_exposure = day_exposure = 0.0
            for other, definition in self.metadata.items():
                exposure = float(self.position_quantity(other)) * max(
                    [
                        p.avg_px_open
                        for p in self.engine.cache.positions_open()
                        if p.instrument_id == self.instruments[other].id
                    ]
                    or [0]
                )
                exposure += sum(
                    float(o.leaves_qty) * float(o.price)
                    for o in existing
                    if o.instrument_id == self.instruments[other].id and o.side == OrderSide.BUY
                )
                if definition["condition_id"] == row["condition_id"]:
                    bin_exposure += exposure
                if definition["local_day"] == row["local_day"]:
                    day_exposure += exposure
            if bin_exposure + cost > 5 + 1e-9 or day_exposure + cost > 20 + 1e-9:
                raise ValueError("Combined YES/NO bin or airport-day exposure limit")
        self.owner[request_id] = owner
        order = self.control.order_factory.limit(
            instrument_id=ins.id,
            order_side=OrderSide[side],
            quantity=ins.make_qty(quantity),
            price=ins.make_price(price),
            time_in_force=TimeInForce[tif],
            expire_time=expire,
            post_only=post_only,
            client_order_id=ClientOrderId(request_id),
            tags=[owner],
        )
        self.control.submit_order(order)

    def snapshot(self):
        account = self.engine.cache.account_for_venue(VENUE)
        cash = float(account.balance_total(pUSD)) if account else self.cash_start
        positions, orders, fills = [], [], []
        realized = fees = market_value = 0.0
        complete = True
        lifecycles = self.engine.cache.positions()
        if self.projection_version >= 2:
            archived = self.engine.cache.position_snapshots()
            lifecycles = list({(str(p.id), p.ts_opened): p for p in archived + lifecycles}.values())
        for p in lifecycles:
            token = self.engine.cache.instrument(p.instrument_id).raw_symbol.value
            definition = self.metadata[token]
            realized += float(p.realized_pnl) if p.realized_pnl else 0
            fees += sum(float(c) for c in p.commissions())
            if not p.is_open:
                continue
            quote = self.quotes.get(token)
            valid = quote and self.now - quote["ts"] <= 30_000_000_000
            bid = quote["best_bid"] if valid else None
            quantity = float(p.quantity)
            if bid is None:
                complete = False
            else:
                market_value += quantity * bid
            positions.append(
                {
                    "token": token,
                    "date": definition["local_day"],
                    "bin": definition["label"],
                    "condition": definition["condition_id"],
                    "outcome": definition["outcome"],
                    "quantity": quantity,
                    "cost": p.avg_px_open * quantity,
                    "bid": bid,
                    "unrealized_pnl": quantity * (bid - p.avg_px_open) if valid else None,
                    "strategy": str(p.strategy_id),
                    "settlement": "pending",
                    "price_status": "valid" if valid else "stale / missing",
                }
            )
        reserved = 0.0
        for order in self.engine.cache.orders():
            if not order.is_closed and order.side == OrderSide.BUY:
                reserved += self.buy_reserve(order)
            if str(order.client_order_id).startswith("EXPIRATION-"):
                order_id = f"settlement-{order.instrument_id}"
            else:
                order_id = str(order.client_order_id)
            orders.append(
                {
                    "order_id": order_id,
                    "token": self.engine.cache.instrument(order.instrument_id).raw_symbol.value,
                    "side": order.side.name,
                    "status": order.status.name,
                    "quantity": float(order.quantity),
                    "filled": float(order.filled_qty),
                    "remaining": float(order.leaves_qty),
                    "price": float(order.price) if hasattr(order, "price") else None,
                    "owner": self.owner.get(order_id, "settlement"),
                    "venue_order_id": str(order.venue_order_id) if order.venue_order_id else None,
                }
            )
            for event in order.events:
                if type(event).__name__ == "OrderFilled":
                    fills.append(
                        {
                            "order_id": order_id,
                            "token": orders[-1]["token"],
                            "ts": event.ts_event,
                            "side": event.order_side.name,
                            "quantity": float(event.last_qty),
                            "price": float(event.last_px),
                            "fee": float(event.commission),
                        }
                    )
        equity = cash + market_value if complete else None
        peak = max(self.equity_peak, equity) if equity is not None else self.equity_peak
        maximum = max(
            self.maximum_drawdown, (peak - equity) / peak if equity is not None and peak else 0
        )
        closed = [p for p in lifecycles if p.is_closed]
        wins = [
            float(p.realized_pnl) for p in closed if p.realized_pnl and float(p.realized_pnl) > 0
        ]
        losses = [
            float(p.realized_pnl) for p in closed if p.realized_pnl and float(p.realized_pnl) < 0
        ]
        return {
            "account": self.config["account"],
            "mode": self.config["mode"],
            "ts": self.now,
            "engine_version": ENGINE_VERSION,
            "cash": cash,
            "reserved": reserved,
            "available": cash - reserved,
            "market_value": market_value if complete else None,
            "equity": equity,
            "realized_pnl": realized,
            "fees": fees,
            "unrealized_pnl": sum(p["unrealized_pnl"] for p in positions) if complete else None,
            "total_pnl": equity - self.cash_start if equity is not None else None,
            "drawdown": (peak - equity) / peak if equity is not None and peak else None,
            "max_drawdown": maximum,
            "positions": positions,
            "orders": orders,
            "fills": fills,
            "rejections": self.rejections,
            "strategy_enabled": self.enabled,
            "trade_count": len(fills),
            "win_rate": len(wins) / len(closed) if closed else None,
            "profit_loss_ratio": (sum(wins) / len(wins)) / (-sum(losses) / len(losses))
            if wins and losses
            else None,
            "sharpe": None,
            "coverage": "Since session start; Sharpe unavailable until 30 complete NY days",
            "settled": self.outcomes,
            "markets": [
                {
                    "token": token,
                    "date": r["local_day"],
                    "bin": r["label"],
                    "outcome": r["outcome"],
                    "condition": r["condition_id"],
                    "minimum_order_size": r["minimum_order_size"],
                    "tick_size": r["tick_size"],
                    "fee_rate": r["fee_rate"],
                    "fee_exponent": r["fee_exponent"],
                    "quote": self.quotes.get(token),
                }
                for token, r in self.metadata.items()
            ],
        }

    def dispose(self):
        self.engine.dispose()


def json_nonempty(value):
    import json

    return bool(json.loads(value))

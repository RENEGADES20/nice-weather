from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from nice_weather.decision import taker_fee_per_share
from nice_weather.domain import (
    ContractBin,
    DecisionOutcome,
    OrderBook,
    PaperOrderStatus,
    SignalAction,
    stable_id,
)
from nice_weather.reason_codes import ReasonCode


@dataclass
class PaperOrder:
    order_id: str
    decision_id: str
    bin_id: str
    side: str
    limit_price: float
    quantity: float
    filled_quantity: float
    average_fill_price: float
    reserved_cash: float
    status: PaperOrderStatus
    created_at: datetime
    updated_at: datetime
    stale_after_cycle: int
    reason_codes: tuple[ReasonCode, ...] = ()


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    order_id: str
    decision_id: str
    bin_id: str
    book_snapshot_id: str
    book_hash: str
    side: str
    price: float
    quantity: float
    fee: float
    filled_at: datetime
    level_index: int


@dataclass
class Position:
    quantity: float = 0.0
    cost_basis: float = 0.0

    @property
    def average_price(self) -> float:
        return self.cost_basis / self.quantity if self.quantity else 0.0


@dataclass(frozen=True)
class PaperAccountSnapshot:
    cash: float
    reserved_cash: float
    used_notional: float
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    nav: float
    positions: dict[str, dict[str, float]]
    scenario_pnl: dict[str, float]
    mark_source: str = "best_bid"


@dataclass
class PaperBroker:
    starting_cash: float
    cash: float | None = None
    realized_pnl: float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    orders: list[PaperOrder] = field(default_factory=list)
    fills: list[PaperFill] = field(default_factory=list)
    _fill_keys: set[tuple[str, str, str, int]] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.starting_cash <= 0:
            raise ValueError("Paper starting cash must be positive")
        if self.cash is None:
            self.cash = self.starting_cash

    def submit(
        self,
        outcome: DecisionOutcome,
        item: ContractBin,
        book: OrderBook,
        decision_time: datetime,
        stale_after_cycle: int,
    ) -> PaperOrder:
        if not outcome.risk_approved or outcome.executable_price is None:
            return self._rejected(outcome, decision_time, stale_after_cycle)
        side = "sell" if outcome.action is SignalAction.EXIT_YES else "buy"
        quantity = outcome.executable_quantity
        order_id = stable_id(
            "paper_order",
            outcome.decision_id,
            outcome.bin_id,
            side,
            f"{quantity:.8f}",
            f"{outcome.executable_price:.8f}",
        )
        existing = next((order for order in self.orders if order.order_id == order_id), None)
        if existing:
            return existing
        required_cash = quantity * outcome.executable_price if side == "buy" else 0.0
        status = PaperOrderStatus.SUBMITTED
        order = PaperOrder(
            order_id=order_id,
            decision_id=outcome.decision_id,
            bin_id=outcome.bin_id,
            side=side,
            limit_price=outcome.executable_price,
            quantity=quantity,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reserved_cash=required_cash,
            status=status,
            created_at=decision_time,
            updated_at=decision_time,
            stale_after_cycle=stale_after_cycle,
        )
        self.orders.append(order)
        position_quantity = self.positions.get(item.bin_id, Position()).quantity
        invalid_sell = side == "sell" and quantity > position_quantity + 1e-9
        if (
            required_cash > float(self.cash) + 1e-9
            or quantity < item.minimum_order_size
            or invalid_sell
        ):
            order.status = PaperOrderStatus.REJECTED
            order.reserved_cash = 0.0
            order.reason_codes = (ReasonCode.PAPER_ORDER_REJECTED,)
            return order
        order.status = PaperOrderStatus.ACCEPTED
        self._match(order, item, book, decision_time)
        return order

    def _rejected(
        self, outcome: DecisionOutcome, decision_time: datetime, stale_after_cycle: int
    ) -> PaperOrder:
        order = PaperOrder(
            order_id=stable_id("paper_order", outcome.decision_id, outcome.bin_id, "rejected"),
            decision_id=outcome.decision_id,
            bin_id=outcome.bin_id,
            side="sell" if outcome.action is SignalAction.EXIT_YES else "buy",
            limit_price=outcome.executable_price or 0.0,
            quantity=outcome.executable_quantity,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reserved_cash=0.0,
            status=PaperOrderStatus.REJECTED,
            created_at=decision_time,
            updated_at=decision_time,
            stale_after_cycle=stale_after_cycle,
            reason_codes=(ReasonCode.PAPER_ORDER_REJECTED,),
        )
        self.orders.append(order)
        return order

    def _match(
        self, order: PaperOrder, item: ContractBin, book: OrderBook, decision_time: datetime
    ) -> None:
        remaining = order.quantity - order.filled_quantity
        previous_cost = order.average_fill_price * order.filled_quantity
        levels = book.asks if order.side == "buy" else book.bids
        for level_index, level in enumerate(levels):
            price_misses_limit = (
                level.price > order.limit_price + 1e-12
                if order.side == "buy"
                else level.price < order.limit_price - 1e-12
            )
            if remaining <= 1e-12 or price_misses_limit:
                break
            key = (order.order_id, book.book_hash, order.side, level_index)
            if key in self._fill_keys:
                continue
            quantity = min(remaining, level.size)
            fee = quantity * taker_fee_per_share(level.price, item.fee_rate, item.fee_exponent)
            total = quantity * level.price + fee
            if order.side == "buy" and total > float(self.cash) + 1e-9:
                quantity = max(0.0, (float(self.cash) - fee) / level.price)
                total = quantity * level.price + fee
            if quantity <= 1e-12:
                break
            fill = PaperFill(
                fill_id=stable_id("paper_fill", *key),
                order_id=order.order_id,
                decision_id=order.decision_id,
                bin_id=order.bin_id,
                book_snapshot_id=book.snapshot_id,
                book_hash=book.book_hash,
                side=order.side,
                price=level.price,
                quantity=quantity,
                fee=fee,
                filled_at=decision_time,
                level_index=level_index,
            )
            self._fill_keys.add(key)
            self.fills.append(fill)
            position = self.positions.setdefault(order.bin_id, Position())
            if order.side == "buy":
                self.cash = float(self.cash) - total
                position.quantity += quantity
                position.cost_basis += total
            else:
                cost_removed = position.average_price * quantity
                proceeds = quantity * level.price - fee
                self.cash = float(self.cash) + proceeds
                position.quantity -= quantity
                position.cost_basis -= cost_removed
                self.realized_pnl += proceeds - cost_removed
                if position.quantity <= 1e-9:
                    position.quantity = 0.0
                    position.cost_basis = 0.0
            previous_cost += quantity * level.price
            order.filled_quantity += quantity
            remaining -= quantity
        if order.filled_quantity:
            order.average_fill_price = previous_cost / order.filled_quantity
        order.reserved_cash = max(0.0, remaining * order.limit_price)
        order.updated_at = decision_time
        if remaining <= 1e-9:
            order.status = PaperOrderStatus.FILLED
        elif order.filled_quantity > 0:
            order.status = PaperOrderStatus.PARTIALLY_FILLED
        else:
            order.status = PaperOrderStatus.ACCEPTED

    def rematch(
        self, order: PaperOrder, item: ContractBin, book: OrderBook, decision_time: datetime
    ) -> None:
        if order.status in (PaperOrderStatus.ACCEPTED, PaperOrderStatus.PARTIALLY_FILLED):
            self._match(order, item, book, decision_time)

    def cancel(self, order: PaperOrder, decision_time: datetime) -> None:
        if order.status in (PaperOrderStatus.ACCEPTED, PaperOrderStatus.PARTIALLY_FILLED):
            order.status = PaperOrderStatus.CANCELED
            order.reserved_cash = 0.0
            order.updated_at = decision_time

    def account_snapshot(
        self, bins: tuple[ContractBin, ...], books: dict[str, OrderBook]
    ) -> PaperAccountSnapshot:
        marks: dict[str, float] = {}
        positions_json: dict[str, dict[str, float]] = {}
        used_notional = unrealized = 0.0
        for item in bins:
            position = self.positions.get(item.bin_id, Position())
            mark = books.get(item.yes_token_id).best_bid if item.yes_token_id in books else 0.0
            mark = mark or 0.0
            marks[item.bin_id] = mark
            used_notional += position.cost_basis
            unrealized += position.quantity * mark - position.cost_basis
            positions_json[item.bin_id] = {
                "label": item.label,
                "quantity": position.quantity,
                "cost_basis": position.cost_basis,
                "average_price": position.average_price,
                "mark": mark,
            }
        reserved = sum(order.reserved_cash for order in self.orders)
        nav = float(self.cash) + sum(
            self.positions.get(item.bin_id, Position()).quantity * marks[item.bin_id]
            for item in bins
        )
        scenarios = {
            item.bin_id: float(self.cash)
            + self.positions.get(item.bin_id, Position()).quantity
            - self.starting_cash
            for item in bins
        }
        return PaperAccountSnapshot(
            cash=float(self.cash),
            reserved_cash=reserved,
            used_notional=used_notional,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized,
            total_pnl=nav - self.starting_cash,
            nav=nav,
            positions=positions_json,
            scenario_pnl=scenarios,
        )

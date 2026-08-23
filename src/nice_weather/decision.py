from __future__ import annotations

from dataclasses import dataclass

from nice_weather.config import CityConfig
from nice_weather.domain import (
    DecisionOutcome,
    HealthLevel,
    MarketContract,
    OrderBook,
    ProbabilityEstimate,
    SignalAction,
)
from nice_weather.reason_codes import ReasonCode


@dataclass(frozen=True)
class ExecutionQuote:
    quantity: float
    vwap: float | None
    depth: float


def depth_quote(book: OrderBook, side: str, requested_quantity: float) -> ExecutionQuote:
    levels = book.asks if side == "buy" else book.bids
    remaining = requested_quantity
    cost = 0.0
    filled = 0.0
    for level in levels:
        take = min(remaining, level.size)
        cost += take * level.price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    return ExecutionQuote(
        filled, cost / filled if filled else None, sum(level.size for level in levels)
    )


def taker_fee_per_share(price: float, rate: float, exponent: float) -> float:
    return rate * (price * (1.0 - price)) ** exponent


def build_outcomes(
    decision_id: str,
    contract: MarketContract,
    estimate: ProbabilityEstimate,
    books: dict[str, OrderBook],
    config: CityConfig,
    health_level: HealthLevel,
    cash_available: float,
    used_notional: float,
    positions: dict[str, float] | None = None,
) -> tuple[DecisionOutcome, ...]:
    positions = positions or {}
    probability_by_bin = {item.bin_id: item.probability for item in estimate.probabilities}
    outcomes: list[DecisionOutcome] = []
    for item in contract.bins:
        reasons: list[ReasonCode] = list(contract.ambiguities)
        book = books.get(item.yes_token_id)
        model_probability = probability_by_bin.get(item.bin_id, 0.0)
        best_bid = book.best_bid if book else None
        best_ask = book.best_ask if book else None
        mid = (best_bid + best_ask) / 2 if best_bid is not None and best_ask is not None else None
        quote = ExecutionQuote(0.0, None, 0.0)
        gross_edge = net_edge = None
        fee = slippage = 0.0
        action = SignalAction.NO_TRADE
        approved = False
        if health_level is HealthLevel.BLOCKED:
            reasons.append(ReasonCode.DATA_STALE)
        if not item.active or item.closed or not item.accepting_orders:
            reasons.append(ReasonCode.MARKET_CLOSED)
        if book is None or best_ask is None or best_bid is None:
            reasons.append(ReasonCode.MARKET_ORDER_BOOK_MISSING)
        elif best_bid >= best_ask:
            reasons.append(ReasonCode.MARKET_ORDER_BOOK_CROSSED)
        else:
            position_quantity = positions.get(item.bin_id, 0.0)
            exit_quote = depth_quote(book, "sell", position_quantity)
            exit_net_edge = None
            if exit_quote.vwap is not None and position_quantity >= item.minimum_order_size:
                exit_gross_edge = best_bid - model_probability
                exit_slippage = best_bid - exit_quote.vwap
                exit_fee = taker_fee_per_share(exit_quote.vwap, item.fee_rate, item.fee_exponent)
                exit_net_edge = (
                    exit_gross_edge - exit_slippage - exit_fee - config.signal.uncertainty_buffer
                )
                if (
                    exit_quote.quantity >= item.minimum_order_size
                    and exit_net_edge >= config.signal.minimum_net_edge
                ):
                    quote = exit_quote
                    gross_edge = exit_gross_edge
                    slippage = exit_slippage
                    fee = exit_fee
                    net_edge = exit_net_edge
                    action = SignalAction.EXIT_YES
                    approved = not reasons
            if action is not SignalAction.EXIT_YES:
                requested_quantity = min(
                    config.signal.target_notional / best_ask,
                    config.paper.max_bin_notional / best_ask,
                )
                quote = depth_quote(book, "buy", requested_quantity)
                if quote.quantity < item.minimum_order_size or quote.vwap is None:
                    reasons.append(ReasonCode.DEPTH_INSUFFICIENT)
                else:
                    gross_edge = model_probability - best_ask
                    slippage = quote.vwap - best_ask
                    fee = taker_fee_per_share(quote.vwap, item.fee_rate, item.fee_exponent)
                    net_edge = gross_edge - slippage - fee - config.signal.uncertainty_buffer
                    notional = quote.quantity * quote.vwap
                    if net_edge < config.signal.minimum_net_edge:
                        reasons.append(ReasonCode.EDGE_BELOW_THRESHOLD)
                    if notional > cash_available + 1e-9:
                        reasons.append(ReasonCode.RISK_CASH_INSUFFICIENT)
                    if used_notional + notional > config.paper.max_city_day_notional + 1e-9:
                        reasons.append(ReasonCode.RISK_CITY_DAY_LIMIT)
                    if not reasons:
                        action = SignalAction.BUY_YES
                        approved = True
        outcomes.append(
            DecisionOutcome(
                decision_id=decision_id,
                bin_id=item.bin_id,
                label=item.label,
                model_probability=model_probability,
                best_bid=best_bid,
                best_ask=best_ask,
                mid=mid,
                executable_quantity=quote.quantity,
                executable_price=quote.vwap,
                executable_depth=quote.depth,
                gross_edge=gross_edge,
                fee=fee,
                slippage=slippage,
                uncertainty_buffer=config.signal.uncertainty_buffer,
                net_edge=net_edge,
                action=action,
                risk_approved=approved,
                reason_codes=tuple(dict.fromkeys(reasons)),
                paper_position=positions.get(item.bin_id, 0.0),
            )
        )
    return tuple(outcomes)

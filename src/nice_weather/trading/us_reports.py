"""Strict venue facts -> Nautilus reports for one YES instrument per US market.

Negative net positions are NO exposure. Missing facts never become zero fills/fees.
The caller owns account authentication, durable evidence, and instrument registration.
"""

from decimal import Decimal, InvalidOperation

import pandas as pd
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import FillReport, OrderStatusReport, PositionStatusReport
from nautilus_trader.model.enums import (
    LiquiditySide,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    TimeInForce,
)
from nautilus_trader.model.identifiers import TradeId, VenueOrderId
from nautilus_trader.model.objects import Money, Price, Quantity


def number(value):
    if isinstance(value, bool):
        raise ValueError("Invalid numeric fact")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid numeric fact") from exc
    if not result.is_finite():
        raise ValueError("Invalid numeric fact")
    return result


def timestamp(value, received_ns):
    if not isinstance(value, str) or not value:
        raise ValueError("Missing source timestamp")
    stamp = pd.Timestamp(value)
    if stamp is pd.NaT or stamp.tzinfo is None or not 0 < stamp.value <= received_ns:
        raise ValueError("Invalid source timestamp")
    return stamp.value


def quantity(value, instrument):
    value = number(value)
    if value < 0:
        raise ValueError("Negative absolute quantity")
    result = Quantity(value, instrument.size_precision)
    if result.as_decimal() != value:
        raise ValueError("Unsupported quantity precision")
    return result


def scope(venue, account, instrument, market, actual_market):
    if (venue not in {"kalshi", "poly_us"} or instrument.outcome != "YES"
            or instrument.id.venue.value != venue.upper()
            or account.get_issuer() != venue.upper()
            or instrument.raw_symbol.value != market
            or instrument.quote_currency.code != "USD"
            or not market or actual_market != market):
        raise ValueError("Account/instrument/market scope mismatch")


def position_report(venue, account, instrument, market, row, received_ns):
    """Map actual signed net quantity; do not synthesize separate YES/NO holdings."""
    if venue == "kalshi":
        key, field, updated = row["ticker"], "position_fp", row["last_updated_ts"]
    elif venue == "poly_us":
        key = row["marketMetadata"]["slug"]
        # The official WS contract documents the old integer value as rounded.
        field = "netPositionDecimal"
        updated = row["updateTime"]
    else:
        raise ValueError("Unsupported venue")
    scope(venue, account, instrument, market, key)
    net = number(row[field])
    return PositionStatusReport(
        account_id=account, instrument_id=instrument.id,
        position_side=PositionSide.LONG if net > 0 else
        PositionSide.SHORT if net < 0 else PositionSide.FLAT,
        quantity=quantity(abs(net), instrument), report_id=UUID4(),
        ts_last=timestamp(updated, received_ns), ts_init=received_ns,
    )


def kalshi_fill_report(account, instrument, market, row, received_ns):
    scope("kalshi", account, instrument, market, row["ticker"])
    if row.get("subaccount_number") != 0:
        raise ValueError("Unsupported subaccount")
    side = {"bid": OrderSide.BUY, "ask": OrderSide.SELL}[row["book_side"]]
    yes, no = number(row["yes_price_dollars"]), number(row["no_price_dollars"])
    if yes + no != 1:
        raise ValueError("Inconsistent outcome prices")
    if "action" in row and "outcome_side" in row:
        action, outcome = row["action"], row["outcome_side"]
        if action not in {"buy", "sell"} or outcome not in {"yes", "no"}:
            raise ValueError("Unsupported order action")
        expected = OrderSide.BUY if (action == "buy") == (outcome == "yes") else OrderSide.SELL
        if side != expected:
            raise ValueError("Inconsistent order action")
    return fill_report(account, instrument, row["order_id"], row["trade_id"], side,
                       row["count_fp"], yes, row["fee_cost"], row["is_taker"],
                       row["created_time"], received_ns)


def kalshi_order_report(account, instrument, market, row, received_ns, *, original_tif):
    """TIF must come from the persisted submitted request; the REST row omits it."""
    scope("kalshi", account, instrument, market, row["ticker"])
    if row.get("subaccount_number") != 0 or row["type"] != "limit":
        raise ValueError("Unsupported order/subaccount")
    if original_tif not in {TimeInForce.GTC, TimeInForce.IOC, TimeInForce.FOK}:
        raise ValueError("Missing original order TIF")
    total = quantity(row["initial_count_fp"], instrument)
    filled = quantity(row["fill_count_fp"], instrument)
    remaining = quantity(row["remaining_count_fp"], instrument)
    if total.as_decimal() <= 0 or filled + remaining > total:
        raise ValueError("Inconsistent order quantities")
    status = {"resting": OrderStatus.ACCEPTED, "executed": OrderStatus.FILLED,
              "canceled": OrderStatus.CANCELED}[row["status"]]
    if status == OrderStatus.FILLED and filled != total:
        raise ValueError("Executed order is not fully filled")
    if status == OrderStatus.ACCEPTED:
        if filled + remaining != total or remaining.as_decimal() <= 0:
            raise ValueError("Inconsistent resting order")
        if filled.as_decimal() > 0:
            status = OrderStatus.PARTIALLY_FILLED
    value = number(row["yes_price_dollars"])
    px = Price(value, instrument.price_precision)
    if not 0 <= value <= 1 or px.as_decimal() != value:
        raise ValueError("Unsupported order price")
    created = timestamp(row["created_time"], received_ns)
    updated = timestamp(row["last_update_time"], received_ns)
    if updated < created:
        raise ValueError("Order update predates creation")
    return OrderStatusReport(
        account_id=account, instrument_id=instrument.id,
        venue_order_id=VenueOrderId(row["order_id"]),
        order_side={"bid": OrderSide.BUY, "ask": OrderSide.SELL}[row["book_side"]],
        order_type=OrderType.LIMIT, time_in_force=original_tif, order_status=status,
        quantity=total, filled_qty=filled, price=px, report_id=UUID4(),
        ts_accepted=created, ts_last=updated, ts_init=received_ns,
    )


def poly_side(order):
    side = {"ORDER_SIDE_BUY": OrderSide.BUY, "ORDER_SIDE_SELL": OrderSide.SELL}[order["side"]]
    expected = {"ORDER_INTENT_BUY_LONG": OrderSide.BUY,
                "ORDER_INTENT_SELL_LONG": OrderSide.SELL,
                "ORDER_INTENT_BUY_SHORT": OrderSide.SELL,
                "ORDER_INTENT_SELL_SHORT": OrderSide.BUY}[order["intent"]]
    if side != expected:
        raise ValueError("Inconsistent order intent")
    if "outcomeSide" in order or "action" in order:
        outcome, action = order["outcomeSide"], order["action"]
        if outcome not in {"OUTCOME_SIDE_YES", "OUTCOME_SIDE_NO"} or action not in {
            "ORDER_ACTION_BUY", "ORDER_ACTION_SELL"
        }:
            raise ValueError("Unsupported order action")
        expected = OrderSide.BUY if (action == "ORDER_ACTION_BUY") == (
            outcome == "OUTCOME_SIDE_YES") else OrderSide.SELL
        if side != expected:
            raise ValueError("Inconsistent order action")
    return side


def poly_fill_report(account, instrument, market, row, received_ns):
    order = row["order"]
    scope("poly_us", account, instrument, market, order["marketSlug"])
    if row["type"] not in {"EXECUTION_TYPE_FILL", "EXECUTION_TYPE_PARTIAL_FILL"}:
        raise ValueError("Not a fill execution")
    if row.get("legPrices"):
        raise ValueError("Combo execution is outside KNYC scope")
    side = poly_side(order)
    px, fee = row["lastPx"], row["commissionNotionalCollected"]
    if px["currency"] != "USD" or fee["currency"] != "USD":
        raise ValueError("Unexpected execution currency")
    return fill_report(account, instrument, order["id"], row["tradeId"], side,
                       row["lastShares"], px["value"], fee["value"], row["aggressor"],
                       row["transactTime"], received_ns)


def poly_order_report(account, instrument, market, row, received_ns, *, source_updated=None):
    scope("poly_us", account, instrument, market, row["marketSlug"])
    if row["type"] != "ORDER_TYPE_LIMIT":
        raise ValueError("Unsupported order type")
    side = poly_side(row)
    tif = {"TIME_IN_FORCE_DAY": TimeInForce.DAY, "TIME_IN_FORCE_GOOD_TILL_CANCEL": TimeInForce.GTC,
           "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL": TimeInForce.IOC,
           "TIME_IN_FORCE_FILL_OR_KILL": TimeInForce.FOK}[row["tif"]]
    status = {"ORDER_STATE_NEW": OrderStatus.ACCEPTED,
              "ORDER_STATE_PENDING_NEW": OrderStatus.SUBMITTED,
              "ORDER_STATE_PENDING_RISK": OrderStatus.SUBMITTED,
              "ORDER_STATE_PENDING_CANCEL": OrderStatus.PENDING_CANCEL,
              "ORDER_STATE_PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
              "ORDER_STATE_FILLED": OrderStatus.FILLED,
              "ORDER_STATE_CANCELED": OrderStatus.CANCELED,
              "ORDER_STATE_REJECTED": OrderStatus.REJECTED,
              "ORDER_STATE_EXPIRED": OrderStatus.EXPIRED}[row["state"]]
    total, filled = quantity(row["quantity"], instrument), quantity(row["cumQuantity"], instrument)
    if total.as_decimal() <= 0 or filled > total:
        raise ValueError("Inconsistent order quantities")
    if status == OrderStatus.FILLED and filled != total:
        raise ValueError("Filled order is not fully filled")
    if status == OrderStatus.PARTIALLY_FILLED and not 0 < filled.as_decimal() < total.as_decimal():
        raise ValueError("Inconsistent partial fill")
    value = number(row["price"]["value"])
    px = Price(value, instrument.price_precision)
    if row["price"]["currency"] != "USD" or not 0 <= value <= 1 or px.as_decimal() != value:
        raise ValueError("Unsupported order price")
    # REST does not provide last transition time; zero means unknown, never backfill it.
    updated = timestamp(source_updated, received_ns) if source_updated is not None else 0
    return OrderStatusReport(
        account_id=account, instrument_id=instrument.id, venue_order_id=VenueOrderId(row["id"]),
        order_side=side, order_type=OrderType.LIMIT, time_in_force=tif, order_status=status,
        quantity=total, filled_qty=filled, price=px, report_id=UUID4(),
        ts_accepted=timestamp(row["insertTime"], received_ns) if row.get("insertTime") else 0,
        ts_last=updated, ts_init=received_ns,
    )


def fill_report(account, instrument, order_id, trade_id, side, qty, price, fee,
                is_taker, updated, received_ns):
    """Shared exact native construction after venue-specific identity/side validation."""
    value, charge = number(price), number(fee)
    if not 0 <= value <= 1 or not isinstance(is_taker, bool) or not order_id or not trade_id:
        raise ValueError("Invalid fill fact")
    px = Price(value, instrument.price_precision)
    commission = Money(charge, instrument.quote_currency)
    if px.as_decimal() != value or commission.as_decimal() != charge:
        raise ValueError("Unsupported price/fee precision")
    size = quantity(qty, instrument)
    if size.as_decimal() <= 0:
        raise ValueError("Empty fill")
    return FillReport(
        account_id=account, instrument_id=instrument.id,
        venue_order_id=VenueOrderId(order_id), trade_id=TradeId(trade_id), order_side=side,
        last_qty=size, last_px=px, commission=commission,
        liquidity_side=LiquiditySide.TAKER if is_taker else LiquiditySide.MAKER,
        report_id=UUID4(), ts_event=timestamp(updated, received_ns), ts_init=received_ns,
    )

from decimal import Decimal

import pytest
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AssetClass, CurrencyType, OrderSide, PositionSide
from nautilus_trader.model.identifiers import AccountId, InstrumentId, Symbol
from nautilus_trader.model.instruments import BinaryOption
from nautilus_trader.model.objects import Currency, Price, Quantity

from nice_weather.trading.us_reports import kalshi_fill_report, position_report

NOW = 1789865000000000000
STAMP = "2026-09-19T23:30:00.123456789Z"


def instrument(venue, currency=USD, outcome="YES"):
    return BinaryOption(
        instrument_id=InstrumentId.from_str("KNYC-TEST." + venue.upper()),
        raw_symbol=Symbol("KNYC-TEST"), asset_class=AssetClass.ALTERNATIVE,
        currency=currency, price_precision=4, price_increment=Price.from_str("0.0001"),
        size_precision=4, size_increment=Quantity.from_str("0.0001"),
        activation_ns=0, expiration_ns=4102444800000000000,
        ts_event=NOW, ts_init=NOW, outcome=outcome,
    )


def test_poly_position_uses_decimal_net_without_fabricating_two_holdings():
    row = {"marketMetadata": {"slug": "KNYC-TEST"}, "updateTime": STAMP,
           "netPosition": "-2", "netPositionDecimal": "-1.5000"}
    report = position_report("poly_us", AccountId("POLY_US-knyc"), instrument("poly_us"),
                             "KNYC-TEST", row, NOW)
    assert report.position_side == PositionSide.SHORT
    assert report.signed_decimal_qty == Decimal("-1.5")
    assert report.ts_last == 1789860600123456789
    assert report.avg_px_open is None
    del row["netPositionDecimal"]
    with pytest.raises(KeyError):
        position_report("poly_us", AccountId("POLY_US-knyc"), instrument("poly_us"),
                        "KNYC-TEST", row, NOW)


@pytest.mark.parametrize("net,side", [("0", PositionSide.FLAT), ("2.01", PositionSide.LONG)])
def test_kalshi_net_positions(net, side):
    row = {"ticker": "KNYC-TEST", "position_fp": net, "last_updated_ts": STAMP}
    report = position_report("kalshi", AccountId("KALSHI-knyc"), instrument("kalshi"),
                             "KNYC-TEST", row, NOW)
    assert report.position_side == side
    assert report.signed_decimal_qty == Decimal(net)


def fill():
    return {"ticker": "KNYC-TEST", "subaccount_number": 0, "order_id": "order-1",
            "trade_id": "trade-1", "book_side": "ask", "action": "buy",
            "outcome_side": "no", "yes_price_dollars": ".6000",
            "no_price_dollars": ".4000", "count_fp": "1.50", "fee_cost": ".0001",
            "is_taker": True, "created_time": STAMP}


def test_kalshi_buy_no_is_sell_yes_and_fee_is_exact():
    usd4 = Currency("USD", 4, 840, "US Dollar", CurrencyType.FIAT)
    report = kalshi_fill_report(AccountId("KALSHI-knyc"), instrument("kalshi", usd4),
                                "KNYC-TEST", fill(), NOW)
    assert report.order_side == OrderSide.SELL
    assert report.last_px.as_decimal() == Decimal(".6")
    assert report.last_qty.as_decimal() == Decimal("1.5")
    assert report.commission.as_decimal() == Decimal(".0001")
    assert report.ts_event == 1789860600123456789
    with pytest.raises(ValueError, match="precision"):
        kalshi_fill_report(AccountId("KALSHI-knyc"), instrument("kalshi"),
                           "KNYC-TEST", fill(), NOW)


@pytest.mark.parametrize("change", [
    {"ticker": "KLGA-TEST"}, {"subaccount_number": 1}, {"book_side": "bid"},
    {"no_price_dollars": ".5"}, {"count_fp": "NaN"}, {"count_fp": "0"},
    {"count_fp": ".00001"}, {"is_taker": "true"}, {"trade_id": ""},
    {"created_time": "2026-09-22T00:00:00Z"}, {"created_time": "2026-09-19"},
])
def test_inconsistent_or_inexact_fills_are_not_native_facts(change):
    row = fill() | {"fee_cost": ".01"} | change
    with pytest.raises((ValueError, KeyError)):
        kalshi_fill_report(AccountId("KALSHI-knyc"), instrument("kalshi"),
                           "KNYC-TEST", row, NOW)


def test_report_rejects_paper_or_wrong_outcome_scope():
    row = fill() | {"fee_cost": ".01"}
    for account, ins in [(AccountId("PAPER-knyc"), instrument("kalshi")),
                         (AccountId("KALSHI-knyc"), instrument("kalshi", outcome="NO"))]:
        with pytest.raises(ValueError, match="scope"):
            kalshi_fill_report(account, ins, "KNYC-TEST", row, NOW)


def test_kalshi_terminal_partial_order_keeps_fills_and_requires_original_tif():
    from nautilus_trader.model.enums import OrderStatus, TimeInForce

    from nice_weather.trading.us_reports import kalshi_order_report

    row = fill() | {"type": "limit", "status": "canceled", "initial_count_fp": "2",
                    "fill_count_fp": ".50", "remaining_count_fp": "0",
                    "last_update_time": STAMP}
    args = (AccountId("KALSHI-knyc"), instrument("kalshi"), "KNYC-TEST", row, NOW)
    report = kalshi_order_report(*args, original_tif=TimeInForce.IOC)
    assert report.order_status == OrderStatus.CANCELED
    assert report.filled_qty.as_decimal() == Decimal(".50")
    assert report.time_in_force == TimeInForce.IOC
    with pytest.raises(ValueError, match="TIF"):
        kalshi_order_report(*args, original_tif=None)
    row["status"] = "executed"
    with pytest.raises(ValueError, match="fully filled"):
        kalshi_order_report(*args, original_tif=TimeInForce.IOC)


def test_poly_partial_fill_uses_actual_fee_and_yes_price():
    from nice_weather.trading.us_reports import poly_fill_report

    row = {"order": {"id": "p-order", "marketSlug": "KNYC-TEST", "side": "ORDER_SIDE_SELL",
                     "intent": "ORDER_INTENT_BUY_SHORT"},
           "type": "EXECUTION_TYPE_PARTIAL_FILL", "tradeId": "p-trade", "lastShares": ".50",
           "lastPx": {"currency": "USD", "value": ".60"},
           "commissionNotionalCollected": {"currency": "USD", "value": "-.01"},
           "aggressor": False, "transactTime": STAMP}
    args = (AccountId("POLY_US-knyc"), instrument("poly_us"), "KNYC-TEST", row, NOW)
    report = poly_fill_report(*args)
    assert report.last_px.as_decimal() == Decimal(".60")
    assert report.last_qty.as_decimal() == Decimal(".50")
    assert report.order_side == OrderSide.SELL
    assert report.commission.as_decimal() == Decimal("-.01")
    row["type"] = "EXECUTION_TYPE_NEW"
    with pytest.raises(ValueError, match="Not a fill"):
        poly_fill_report(*args)
    row["type"] = "EXECUTION_TYPE_FILL"
    del row["commissionNotionalCollected"]
    with pytest.raises(KeyError):
        poly_fill_report(*args)


def test_poly_rest_status_does_not_invent_source_transition_time():
    from nautilus_trader.model.enums import OrderStatus

    from nice_weather.trading.us_reports import poly_order_report

    row = {"id": "p-order", "marketSlug": "KNYC-TEST", "side": "ORDER_SIDE_BUY",
           "intent": "ORDER_INTENT_BUY_LONG",
           "type": "ORDER_TYPE_LIMIT", "tif": "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL",
           "state": "ORDER_STATE_CANCELED", "quantity": "2", "cumQuantity": ".5",
           "price": {"currency": "USD", "value": ".4"}, "insertTime": STAMP}
    args = (AccountId("POLY_US-knyc"), instrument("poly_us"), "KNYC-TEST", row, NOW)
    report = poly_order_report(*args)
    assert report.order_status == OrderStatus.CANCELED
    assert report.filled_qty.as_decimal() == Decimal(".5")
    assert report.ts_last == 0
    assert report.ts_init == NOW
    row["state"] = "ORDER_STATE_FILLED"
    with pytest.raises(ValueError, match="fully filled"):
        poly_order_report(*args)

"""Validate complete exchange facts before Nautilus can reconcile or infer any fill."""

from decimal import Decimal
from types import SimpleNamespace

from nautilus_trader.core.uuid import UUID4
from nautilus_trader.execution.reports import ExecutionMassStatus
from nautilus_trader.model.enums import OrderSide, OrderStatus, PositionSide

from nice_weather.trading import live_budget
from nice_weather.trading.storage import connect
from nice_weather.trading.us_reports import kalshi_fill_report, poly_fill_report, position_report


async def mass_status(client, history=None):
    orders = await client.generate_order_status_reports(
        SimpleNamespace(instrument_id=None, start=None, end=None, open_only=False, history=history)
    )
    history = client.last_history
    received = int(history["history_received_at"] * 1e9)
    registry = {c["condition_id"]: (i, c) for i, c in client.markets.values()}
    kalshi = client.transport.venue == "kalshi"
    owned = {r.venue_order_id.value: r for r in orders}
    fills, seen = [], {}
    for row in history.get("fills" if kalshi else "executions", []):
        key = row["ticker"] if kalshi else row["order"]["marketSlug"]
        remote = row["order_id"] if kalshi else row["order"]["id"]
        if key not in registry or remote not in owned:
            continue
        instrument, _ = registry[key]
        convert = kalshi_fill_report if kalshi else poly_fill_report
        report = convert(client.account_id, instrument, key, row, received)
        identity = (remote, report.trade_id.value)
        facts = (
            report.last_qty,
            report.last_px,
            report.commission,
            report.order_side,
            report.ts_event,
        )
        if identity in seen:
            if seen[identity] != facts:
                raise ValueError("CONFLICTING_FILL")
            continue
        seen[identity] = facts
        fills.append(report)
    fills.sort(key=lambda f: (f.ts_event, f.trade_id.value))
    for order in orders:
        actual = [f for f in fills if f.venue_order_id == order.venue_order_id]
        if (
            sum((f.last_qty.as_decimal() for f in actual), Decimal(0))
            != order.filled_qty.as_decimal()
        ):
            raise ValueError("FILL_HISTORY_INCOMPLETE")
        if any(f.order_side != order.order_side for f in actual):
            raise ValueError("FILL_ORDER_SIDE_MISMATCH")

    positions = []
    for row in history["positions"]:
        key = (
            row["ticker"]
            if kalshi
            else row.get("marketSlug", row.get("marketMetadata", {}).get("slug"))
        )
        if key not in registry:
            continue
        ins, _ = registry[key]
        positions.append(
            position_report(client.transport.venue, client.account_id, ins, key, row, received)
        )
    actual_net = {r.instrument_id: r.signed_decimal_qty for r in positions}
    if len(actual_net) != len(positions):
        raise ValueError("DUPLICATE_POSITION")
    # An unexplained external opening position must not generate synthetic fills.
    for ins_id, (ins, _) in client.markets.items():
        expected = sum(
            (
                f.last_qty.as_decimal() * (1 if f.order_side == OrderSide.BUY else -1)
                for f in fills
                if f.instrument_id == ins_id
            ),
            Decimal(0),
        )
        if expected != actual_net.get(ins_id, Decimal(0)):
            raise ValueError("POSITION_HISTORY_MISMATCH")
        if ins_id not in actual_net:
            from nautilus_trader.execution.reports import PositionStatusReport
            from nautilus_trader.model.objects import Quantity

            positions.append(
                PositionStatusReport(
                    account_id=client.account_id,
                    instrument_id=ins_id,
                    position_side=PositionSide.FLAT,
                    quantity=Quantity(0, ins.size_precision),
                    report_id=UUID4(),
                    ts_last=0,
                    ts_init=received,
                )
            )
    report = ExecutionMassStatus(
        client_id=client.id,
        account_id=client.account_id,
        venue=client.venue,
        report_id=UUID4(),
        ts_init=received,
    )
    report.add_order_reports(orders)
    report.add_fill_reports(fills)
    report.add_position_reports(positions)
    client.budget_updates = []
    terminal = {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
    for order in orders:
        attempt = client.transport.attempt(order.client_order_id.value)
        intent = attempt["evidence"]
        total = Decimal(0)
        for fill in fills:
            if fill.venue_order_id != order.venue_order_id:
                continue
            px = fill.last_px.as_decimal()
            if intent["outcome"] == "NO":
                px = 1 - px
            total += max(Decimal(0), fill.commission.as_decimal())
            if intent["side"] == "BUY":
                total += px * fill.last_qty.as_decimal()
        client.budget_updates.append(
            (order.client_order_id.value, total, order.order_status in terminal)
        )
    client.terminal_order_ids = {
        o.venue_order_id.value for o in orders if o.order_status in terminal
    }
    return report


def reconcile_budget(client):
    """Called only after the native engine accepted the validated mass report."""
    with connect(client.transport.path) as con:
        con.execute("BEGIN IMMEDIATE")
        for request_id, spent, terminal in client.budget_updates:
            row = con.execute(
                "SELECT * FROM live_test_budget WHERE account=? AND request_id=?",
                (client.transport.account, request_id),
            ).fetchone()
            if not row:
                raise ValueError("BUDGET_RECORD_MISSING")
            # Keep the unspent bound until finality; partial fills cannot release too much.
            remaining = (
                Decimal(0)
                if terminal
                else (Decimal(row["reserved"]) + Decimal(row["spent"]) - spent)
            )
            live_budget.reconcile(
                con, client.transport.account, request_id, spent, remaining, terminal=terminal
            )
        for attempt in client.transport.attempts():
            if attempt["status"] not in {"unknown", "submitting"}:
                continue
            evidence = attempt.get("evidence") or {}
            path = evidence.get("path", "")
            cancel = evidence.get("method") == "DELETE" or path.endswith("/cancel")
            remote = path.split("/")[-2 if path.endswith("/cancel") else -1]
            if cancel and remote in client.terminal_order_ids:
                from nice_weather.trading.storage import encoded

                response = {"order_id": remote} if client.transport.venue == "kalshi" else {}
                con.execute(
                    "UPDATE transport_attempts SET status='accepted',response=?,error=NULL "
                    "WHERE account=? AND request_id=?",
                    (encoded(response), client.transport.account, attempt["request_id"]),
                )

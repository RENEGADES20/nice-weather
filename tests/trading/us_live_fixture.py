"""Local-only deterministic exchange transport; never packaged or deployed."""

import base64
import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nice_weather.trading.us_transport import USRest


class Exchange:
    def __init__(self, root, venue="poly_us"):
        self.venue = venue
        self.fills = []
        self.orders, self.net, self.cash, self.writes = {}, Decimal(0), Decimal(100), 0
        self.path = root / "mock-exchange.json"
        if self.path.exists():
            saved = json.loads(self.path.read_text())
            self.orders, self.writes = saved["orders"], saved["writes"]
            self.net, self.cash = Decimal(saved["net"]), Decimal(saved["cash"])
            self.fills = saved.get("fills", [])
        secret = (
            base64.b64encode(Ed25519PrivateKey.generate().private_bytes_raw()).decode()
            if venue == "poly_us"
            else rsa.generate_private_key(public_exponent=65537, key_size=2048)
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
        self.transport = USRest(
            venue,
            "live-" + venue + "-knyc",
            "fixture-key",
            secret,
            root / "results.sqlite3",
            transport=httpx.MockTransport(self.handle),
        )
        self.contract = {
            "venue": venue,
            "station_id": "KNYC",
            "condition_id": "KNYC-TEST",
            "quantity_step": ".01",
            "tick_size": ".01",
            "minimum_order_size": ".01",
            "parse_status": "parsed",
            "fee_known": True,
            "fee_rate": "0.0695",
            "fee_exponent": 1,
            "fee_rounding": "poly_us_order_half_even_v1",
            "active": True,
            "accepting_orders": True,
        }
        if venue == "kalshi":
            self.contract.update(fee_rounding="exact", exchange_index=0)

    def handle(self, request):
        if self.venue == "kalshi":
            return self.kalshi(request)
        path = request.url.path
        stamp = datetime.now(UTC).isoformat()
        if request.method == "GET":
            if path.endswith("/account/balances"):
                return httpx.Response(
                    200,
                    json={
                        "balances": [
                            {
                                "currency": "USD",
                                "currentBalance": str(self.cash),
                                "buyingPower": str(self.cash),
                                "assetNotional": "0",
                                "balanceReservation": "0",
                                "openOrders": "0",
                            }
                        ]
                    },
                )
            if path.endswith("/orders/open"):
                return httpx.Response(
                    200,
                    json={
                        "orders": [
                            r
                            for r in self.orders.values()
                            if r["state"] in {"ORDER_STATE_NEW", "ORDER_STATE_PARTIALLY_FILLED"}
                        ]
                    },
                )
            if "/order/" in path:
                return httpx.Response(200, json={"order": self.orders[path.split("/")[-1]]})
            if path.endswith("/portfolio/positions"):
                return httpx.Response(
                    200,
                    json={
                        "positions": []
                        if not self.net
                        else [
                            {
                                "marketSlug": "KNYC-TEST",
                                "marketMetadata": {"slug": "KNYC-TEST"},
                                "netPositionDecimal": str(self.net),
                                "updateTime": stamp,
                            }
                        ]
                    },
                )
            if path.endswith("/portfolio/activities"):
                return httpx.Response(200, json={"activities": []})
        body = json.loads(request.content)
        if request.method == "POST" and path == "/v1/orders":
            self.writes += 1
            oid = "remote-" + str(self.writes)
            yes, buy = (
                body["outcomeSide"] == "OUTCOME_SIDE_YES",
                body["action"] == "ORDER_ACTION_BUY",
            )
            side = "ORDER_SIDE_BUY" if yes == buy else "ORDER_SIDE_SELL"
            intent = "ORDER_INTENT_" + ("BUY" if buy else "SELL") + ("_LONG" if yes else "_SHORT")
            q, px = Decimal(str(body["quantity"])), Decimal(body["price"]["value"])
            # GTC leaves a genuine partial remainder; IOC/FOK fixture fills completely.
            fill = q / 2 if body["tif"] == "TIME_IN_FORCE_GOOD_TILL_CANCEL" else q
            row = body | {
                "id": oid,
                "side": side,
                "intent": intent,
                "insertTime": stamp,
                "quantity": str(q),
                "cumQuantity": str(fill),
                "state": "ORDER_STATE_PARTIALLY_FILLED" if fill < q else "ORDER_STATE_FILLED",
            }
            self.orders[oid] = row
            self.net += fill * (1 if yes == buy else -1)
            cost = fill * (px if yes else 1 - px)
            self.cash += cost * (-1 if buy else 1) - Decimal(".01")
            self.transport.save_execution(
                {
                    "order": row,
                    "type": "EXECUTION_TYPE_FILL" if q == fill else "EXECUTION_TYPE_PARTIAL_FILL",
                    "tradeId": "trade-" + oid,
                    "lastShares": str(fill),
                    "lastPx": body["price"],
                    "commissionNotionalCollected": {"currency": "USD", "value": ".01"},
                    "aggressor": True,
                    "transactTime": stamp,
                }
            )
            self.save()
            return httpx.Response(201, json={"id": oid})
        if request.method == "POST" and path.endswith("/cancel"):
            self.orders[path.split("/")[-2]]["state"] = "ORDER_STATE_CANCELED"
            self.save()
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected fixture request: {request.method} {path}")

    def save(self):
        self.path.write_text(
            json.dumps(
                {
                    "orders": self.orders,
                    "writes": self.writes,
                    "net": str(self.net),
                    "cash": str(self.cash),
                    "fills": self.fills,
                }
            )
        )

    def kalshi(self, request):
        path = request.url.path.removeprefix("/trade-api/v2")
        stamp = datetime.now(UTC).isoformat()
        if request.method == "GET":
            if path == "/portfolio/balance":
                return httpx.Response(
                    200,
                    json={
                        "balance_dollars": str(self.cash),
                        "portfolio_value": 0,
                        "updated_ts": int(datetime.now(UTC).timestamp()),
                        "balance_breakdown": [{"exchange_index": 0, "balance": str(self.cash)}],
                    },
                )
            if path.startswith("/markets/"):
                return httpx.Response(
                    200, json={"market": {"ticker": "KNYC-TEST", "exchange_index": 0}}
                )
            if path == "/portfolio/orders":
                return httpx.Response(200, json={"orders": list(self.orders.values())})
            if path.startswith("/portfolio/orders/"):
                return httpx.Response(200, json={"order": self.orders[path.split("/")[-1]]})
            if path == "/portfolio/positions":
                return httpx.Response(
                    200,
                    json={
                        "market_positions": []
                        if not self.net
                        else [
                            {
                                "ticker": "KNYC-TEST",
                                "position_fp": str(self.net),
                                "last_updated_ts": stamp,
                            }
                        ]
                    },
                )
            if path == "/portfolio/fills":
                return httpx.Response(200, json={"fills": self.fills})
            if path == "/historical/cutoff":
                return httpx.Response(200, json={"orders_updated_ts": 1})
            if path.startswith("/historical/"):
                return httpx.Response(
                    200,
                    json={
                        "market_positions"
                        if path.endswith("positions")
                        else path.split("/")[-1]: []
                    },
                )
        if request.method == "POST" and path == "/portfolio/events/orders":
            body = json.loads(request.content)
            self.writes += 1
            oid = "remote-" + str(self.writes)
            q, p = Decimal(body["count"]), Decimal(body["price"])
            filled = q / 2 if body["time_in_force"] == "good_till_canceled" else q
            row = {
                "order_id": oid,
                "ticker": body["ticker"],
                "book_side": body["side"],
                "client_order_id": body["client_order_id"],
                "type": "limit",
                "subaccount_number": 0,
                "initial_count_fp": str(q),
                "fill_count_fp": str(filled),
                "remaining_count_fp": str(q - filled),
                "yes_price_dollars": str(p),
                "no_price_dollars": str(1 - p),
                "created_time": stamp,
                "last_update_time": stamp,
                "status": "resting" if filled < q else "executed",
            }
            self.orders[oid] = row
            self.fills.append(
                row
                | {
                    "count_fp": str(filled),
                    "trade_id": "trade-" + oid,
                    "fill_id": "fill-" + oid,
                    "fee_cost": ".01",
                    "is_taker": True,
                }
            )
            self.net += filled * (1 if body["side"] == "bid" else -1)
            self.cash -= filled * p + Decimal(".01")  # Fixture display only; not a cash model.
            self.save()
            return httpx.Response(201, json={"order_id": oid})
        if request.method == "DELETE" and path.startswith("/portfolio/events/orders/"):
            oid = path.split("/")[-1]
            self.orders[oid].update(
                status="canceled", remaining_count_fp="0", last_update_time=stamp
            )
            self.save()
            return httpx.Response(200, json={"order_id": oid})
        raise AssertionError(f"Unexpected fixture request: {request.method} {path}")

"""Signed US venue transport and durable command attempts (not an account ledger).

No credentials are loaded on import. Native execution clients own order/account facts.
Writes remain disabled unless their caller supplies a current risk/activation gate.
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from nice_weather.trading.storage import connect, digest, encoded

HOSTS = {"kalshi": "https://external-api.kalshi.com", "poly_us": "https://api.polymarket.us"}
PREFIXES = {"kalshi": "/trade-api/v2", "poly_us": "/v1"}


def amount(value, step, *, upper=None):
    if isinstance(value, bool):
        raise ValueError("Invalid amount")
    try:
        d, quantum = Decimal(str(value)), Decimal(str(step))
    except InvalidOperation as exc:
        raise ValueError("Invalid amount/precision") from exc
    if not quantum.is_finite() or quantum <= 0:
        raise ValueError("Invalid amount/precision")
    if not d.is_finite() or d <= 0 or (upper is not None and d >= upper):
        raise ValueError("Invalid amount/precision")
    try:
        if d % quantum:
            raise ValueError("Invalid amount/precision")
    except InvalidOperation as exc:
        raise ValueError("Invalid amount/precision") from exc
    return d


def order_body(venue, request_id, contract, order):
    if venue not in HOSTS or contract.get("venue") != venue:
        raise ValueError("Venue mismatch")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
        raise ValueError("Invalid request ID")
    market = contract["condition_id"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", market):
        raise ValueError("Invalid market identifier")
    outcome, side, tif = order["outcome"], order["side"], order.get("tif", "IOC")
    if (
        outcome not in {"YES", "NO"}
        or side not in {"BUY", "SELL"}
        or tif not in {"GTC", "IOC", "FOK"}
    ):
        raise ValueError("Unsupported order")
    p = amount(order["price"], contract["tick_size"], upper=1)
    q = amount(order["quantity"], contract["quantity_step"])
    minimum = amount(contract["minimum_order_size"], contract["quantity_step"])
    if q < minimum:
        raise ValueError("Below market minimum")
    if venue == "kalshi":
        # The wire formats have fixed precision; never round a trader's instruction.
        amount(p, ".0001", upper=1)
        amount(q, ".01")
        return {
            "ticker": market,
            "client_order_id": request_id,
            "side": "bid" if (side == "BUY") == (outcome == "YES") else "ask",
            "price": format(p if outcome == "YES" else 1 - p, ".4f"),
            "count": format(q, ".2f"),
            "time_in_force": {
                "GTC": "good_till_canceled",
                "IOC": "immediate_or_cancel",
                "FOK": "fill_or_kill",
            }[tif],
            "self_trade_prevention_type": "taker_at_cross",
            "cancel_order_on_pause": True,
            "reduce_only": side == "SELL",
            "subaccount": 0,
        }
    # No documented client_order_id on Poly US; a timeout must never be retried.
    wire_quantity = float(q)
    if not math.isfinite(wire_quantity) or Decimal(str(wire_quantity)) != q:
        raise ValueError("Quantity cannot be represented by venue JSON number")
    return {
        "marketSlug": market,
        "type": "ORDER_TYPE_LIMIT",
        # US has one YES instrument; even NO intents carry the YES-side limit price.
        "price": {"value": str(p if outcome == "YES" else 1 - p), "currency": "USD"},
        "quantity": wire_quantity,
        "outcomeSide": "OUTCOME_SIDE_" + outcome,
        "action": "ORDER_ACTION_" + side,
        "tif": "TIME_IN_FORCE_"
        + {"GTC": "GOOD_TILL_CANCEL", "IOC": "IMMEDIATE_OR_CANCEL", "FOK": "FILL_OR_KILL"}[tif],
        "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_"
        + ("MANUAL" if order.get("owner", "manual") == "manual" else "AUTOMATIC"),
    }


class USRest:
    def __init__(
        self, venue, account, key_id, secret, attempts: Path, *, gate=None, transport=None
    ):
        if venue not in HOSTS or not re.fullmatch(
            "live-" + venue + r"-[A-Za-z0-9_-]{1,48}", account
        ):
            raise ValueError("Live venue/account namespace required")
        if not isinstance(key_id, str) or not key_id or any(ord(c) < 33 for c in key_id):
            raise ValueError("Invalid key ID")
        self.venue, self.account, self.key_id, self.path = venue, account, key_id, attempts
        if venue == "kalshi":
            self.key = serialization.load_pem_private_key(secret.encode(), password=None)
            if not isinstance(self.key, rsa.RSAPrivateKey) or self.key.key_size < 2048:
                raise ValueError("Kalshi requires an RSA private key")
        else:
            raw = base64.b64decode(secret, validate=True)
            if len(raw) not in (32, 64):
                raise ValueError("Invalid Poly US Ed25519 key")
            self.key = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])
        self.gate = gate
        # Never follow a redirect with authentication material; no automatic HTTP retry.
        self.client = httpx.AsyncClient(timeout=10, follow_redirects=False, transport=transport)
        with connect(attempts) as con:
            from nice_weather.trading.live_budget import install

            install(con)
            con.execute("""CREATE TABLE IF NOT EXISTS transport_attempts (
                account TEXT NOT NULL, request_id TEXT NOT NULL, identity TEXT NOT NULL,
                status TEXT NOT NULL, response TEXT, error TEXT, updated REAL NOT NULL,
                PRIMARY KEY(account,request_id))""")

    def headers(self, method, path, timestamp=None):
        stamp = str(int(time.time() * 1000) if timestamp is None else timestamp)
        message = (stamp + method + path.split("?", 1)[0]).encode()
        if self.venue == "kalshi":
            signed = self.key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256(),
            )
            names = ("KALSHI-ACCESS-KEY", "KALSHI-ACCESS-TIMESTAMP", "KALSHI-ACCESS-SIGNATURE")
        else:
            signed = self.key.sign(message)
            names = ("X-PM-Access-Key", "X-PM-Timestamp", "X-PM-Signature")
        return dict(
            zip(names, (self.key_id, stamp, base64.b64encode(signed).decode()), strict=True)
        )

    async def read(self, path, params=None):
        full = self._path(path)
        response = await self.client.get(
            HOSTS[self.venue] + full, params=params, headers=self.headers("GET", full)
        )
        if response.status_code != 200:
            raise ValueError(f"Venue read rejected: HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Invalid venue response")
        return payload

    async def account_snapshot(self):
        """Read venue facts; do not invent a native balance or reset order uncertainty."""
        import asyncio

        if self.venue == "kalshi":
            balance, orders, positions = await asyncio.gather(
                self.read("/portfolio/balance"),
                self._pages("/portfolio/orders", "orders"),
                self._pages("/portfolio/positions", "market_positions"),
            )
        else:
            balance, orders, positions = await asyncio.gather(
                self.read("/account/balances"),
                self.read("/orders/open"),
                self._pages("/portfolio/positions", "positions"),
            )
        return {
            "venue": self.venue,
            "account": self.account,
            "received_at": time.time(),
            "balance": balance,
            "orders": orders,
            "positions": positions,
            "reconciled": False,
        }

    async def _pages(self, path, key):
        result, cursors, cursor = [], set(), None
        for _ in range(100):
            payload = await self.read(
                path, {"limit": 100, **({"cursor": cursor} if cursor else {})}
            )
            rows = payload[key]
            if isinstance(rows, dict) and self.venue == "poly_us" and key == "positions":
                if any(
                    not isinstance(value, dict) or value.get("marketSlug", market) != market
                    for market, value in rows.items()
                ):
                    raise ValueError("Position identity mismatch")
                rows = [{**value, "marketSlug": market} for market, value in rows.items()]
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise ValueError("Invalid paginated venue response")
            result.extend(rows)
            cursor = payload.get("cursor" if self.venue == "kalshi" else "nextCursor")
            if not cursor or payload.get("eof") is True:
                return result
            if not isinstance(cursor, str) or cursor in cursors:
                raise ValueError("Pagination did not advance; incomplete reconciliation")
            cursors.add(cursor)
        raise ValueError("Account history exceeds bounded read; reconciliation incomplete")

    async def account_history(self):
        """Read both historical tiers; never treat a missing current order as cancelled."""
        import asyncio

        snapshot = await self.account_snapshot()
        if self.venue == "kalshi":
            cutoff = await self.read("/historical/cutoff")
            old_orders, fills, old_fills, old_positions = await asyncio.gather(
                self._pages("/historical/orders", "orders"),
                self._pages("/portfolio/fills", "fills"),
                self._pages("/historical/fills", "fills"),
                self._pages("/historical/positions", "market_positions"),
            )
            if cutoff != await self.read("/historical/cutoff"):
                raise ValueError("History cutoff moved during reconciliation")

            def combine(rows, identity):
                result = {}
                for row in rows:
                    key = row.get(identity)
                    if not isinstance(key, str) or not key:
                        raise ValueError("Missing historical record identity")
                    if key in result and result[key] != row:
                        raise ValueError("Conflicting live/historical records")
                    result[key] = row
                return list(result.values())

            snapshot.update(
                orders=combine(snapshot["orders"] + old_orders, "order_id"),
                fills=combine(fills + old_fills, "fill_id"),
                historical_positions=old_positions,
                cutoff=cutoff,
            )
        else:
            snapshot["activities"] = await self._pages("/portfolio/activities", "activities")
        snapshot["history_received_at"] = time.time()
        # Native order/fill/position matching is still required after collecting history.
        return snapshot

    def _path(self, path):
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in path or "//" in path:
            raise ValueError("Invalid API path")
        return PREFIXES[self.venue] + path

    def attempt(self, request_id):
        with connect(self.path, readonly=True) as con:
            row = con.execute(
                "SELECT * FROM transport_attempts WHERE account=? AND request_id=?",
                (self.account, request_id),
            ).fetchone()
        if not row:
            return None
        return dict(row) | {"response": json.loads(row["response"] or "null")}

    async def submit(self, request_id, contract, order):
        if contract.get("parse_status") != "parsed":
            raise ValueError("Unverified contract rules; no-trade")
        body = order_body(self.venue, request_id, contract, order)
        path = "/portfolio/events/orders" if self.venue == "kalshi" else "/orders"
        return await self._write(request_id, "POST", path, body, contract, order)

    async def cancel(self, request_id, contract, venue_order_id):
        if contract.get("venue") != self.venue or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", venue_order_id
        ):
            raise ValueError("Invalid cancellation identity")
        # A stale rule/model or uncertain other order must not prevent cancelling known risk.
        # The caller's gate must still establish account ownership of this exact venue order.
        if self.venue == "kalshi":
            method, path, body = "DELETE", "/portfolio/events/orders/" + venue_order_id, None
        else:
            method, path = "POST", "/order/" + venue_order_id + "/cancel"
            body = {"marketSlug": contract["condition_id"]}
        return await self._write(
            request_id,
            method,
            path,
            body,
            contract,
            {"kind": "cancel", "venue_order_id": venue_order_id},
            cancel_id=venue_order_id,
        )

    async def _write(self, request_id, method, path, body, contract, order, *, cancel_id=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
            raise ValueError("Invalid request ID")
        full = self._path(path)
        identity = digest([method, full, body])
        previous = self.attempt(request_id)
        if previous:
            if previous["identity"] != identity:
                raise ValueError("Request ID reused with different order")
            # Includes an interrupted 'submitting': read/reconcile, never issue another POST.
            return previous | {
                "status": "unknown" if previous["status"] == "submitting" else previous["status"]
            }
        if self.gate is None:
            raise ValueError("Live disabled: activation and risk gate missing")
        if self.gate(self.account, contract, order) is not True:
            raise ValueError("Live activation/risk gate rejected")
        with connect(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            if (
                cancel_id is None
                and con.execute(
                    "SELECT 1 FROM transport_attempts WHERE account=? "
                    "AND status IN ('submitting','unknown') LIMIT 1",
                    (self.account,),
                ).fetchone()
            ):
                raise ValueError("Account has an unresolved submission; reconcile first")
            inserted = con.execute(
                "INSERT OR IGNORE INTO transport_attempts VALUES (?,?,?,'submitting',NULL,NULL,?)",
                (self.account, request_id, identity, time.time()),
            ).rowcount
            if inserted:
                from nice_weather.trading.live_budget import reserve

                reserve(con, self.venue, self.account, request_id, contract, order)
        if not inserted:
            previous = self.attempt(request_id)
            if previous["identity"] != identity:
                raise ValueError("Concurrent request identity collision")
            return previous | {"status": "unknown"}
        status, error, payload = "unknown", None, None
        try:
            response = await self.client.request(
                method, HOSTS[self.venue] + full, json=body, headers=self.headers(method, full)
            )
            if response.status_code in (200, 201):
                payload = response.json()
                expected = "order_id" if self.venue == "kalshi" else "id"
                if cancel_id is not None:
                    if self.venue == "kalshi":
                        if not isinstance(payload, dict) or payload.get("order_id") != cancel_id:
                            raise ValueError("Cancellation order ID mismatch")
                    elif payload != {}:
                        raise ValueError("Invalid cancellation receipt")
                elif (
                    not isinstance(payload, dict)
                    or not isinstance(payload.get(expected), str)
                    or not payload[expected]
                ):
                    raise ValueError("Missing exchange order ID")
                status = "accepted"
            elif response.status_code in (400, 401, 403, 404, 422):
                status, error = "rejected", f"HTTP {response.status_code}"
            else:
                error = f"HTTP {response.status_code}; reconcile before any further order"
        except (httpx.HTTPError, ValueError) as exc:
            error = type(exc).__name__ + "; exchange state unknown"
        finally:
            with connect(self.path) as con:
                if status == "rejected":
                    from nice_weather.trading.live_budget import rejected

                    rejected(con, self.account, request_id)
                con.execute(
                    "UPDATE transport_attempts SET status=?,response=?,error=?,updated=? "
                    "WHERE account=? AND request_id=?",
                    (status, encoded(payload), error, time.time(), self.account, request_id),
                )
        return self.attempt(request_id)

    async def close(self):
        await self.client.aclose()

"""Selected-market depth, memory only. No database or file writer exists here."""

from __future__ import annotations

import json
import logging
import threading
import time
from copy import deepcopy
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from websockets.sync.client import connect


class DepthFeed:
    def __init__(self):
        self.lock = threading.RLock()
        self.books, self.leases, self.allowed = {}, {}, set()
        self.required = set()
        self.closed = threading.Event()
        self.last_error = None

    def wanted(self):
        with self.lock:
            now = time.monotonic()
            self.leases = {k: v for k, v in self.leases.items() if v > now}
            return (set(self.leases) | self.required) & self.allowed

    def view(self, tokens):
        with self.lock:
            if len(tokens) > 4 or set(tokens) - self.allowed:
                raise ValueError("Select up to four verified market outcomes")
            for token in tokens:
                self.leases[token] = time.monotonic() + 20
            return {t: self.book(t) for t in tokens}

    def book(self, token):
        with self.lock:
            row = deepcopy(self.books.get(token, {}))
        if not row:
            return {"token_id": token, "valid": False, "bids": [], "asks": []}
        row["valid"] = bool(
            row.get("valid") and time.time_ns() - row["received_ns"] < 30_000_000_000
        )
        return row

    def invalidate(self):
        with self.lock:
            for row in self.books.values():
                row["valid"] = False

    def ingest(self, event):
        kind = event.get("event_type")
        if kind == "price_change":
            for change in event.get("price_changes", []):
                token = str(change["asset_id"])
                with self.lock:
                    old = self.books.get(token)
                    if not old or not old.get("valid"):
                        continue
                    if change["side"] not in {"BUY", "SELL"}:
                        raise ValueError("Invalid depth side")
                    side = "bids" if change["side"] == "BUY" else "asks"
                    levels = dict(old[side])
                    price = format(Decimal(str(change["price"])).normalize(), "f")
                    size = str(change["size"])
                    if Decimal(size) == 0:
                        levels.pop(price, None)
                    else:
                        levels[price] = size
                    raw = {
                        "asset_id": token,
                        "event_type": "book",
                        "timestamp": event.get("timestamp"),
                    }
                    raw.update(
                        {
                            s: [
                                {"price": p, "size": q}
                                for p, q in (levels.items() if s == side else old[s])
                            ]
                            for s in ("bids", "asks")
                        }
                    )
                    self.ingest(raw)
        elif kind == "book":
            token = str(event.get("asset_id"))
            if token not in self.allowed:
                return
            levels = {}
            for side in ("bids", "asks"):
                values = {}
                for level in event.get(side, []):
                    p, q = Decimal(str(level["price"])), Decimal(str(level["size"]))
                    if not p.is_finite() or not q.is_finite() or not 0 <= p <= 1 or q < 0:
                        raise ValueError("Invalid public depth")
                    if q:
                        values[format(p.normalize(), "f")] = format(q.normalize(), "f")
                levels[side] = sorted(
                    values.items(), key=lambda x: Decimal(x[0]), reverse=side == "bids"
                )
            valid = bool(
                levels["bids"]
                and levels["asks"]
                and Decimal(levels["bids"][0][0]) < Decimal(levels["asks"][0][0])
            )
            with self.lock:
                stamp = int(event.get("timestamp") or 0)
                if stamp < self.books.get(token, {}).get("exchange_ms", 0):
                    return
                self.books[token] = dict(
                    token_id=token,
                    received_ns=time.time_ns(),
                    exchange_ms=stamp,
                    valid=valid,
                    **levels,
                )

    def run(self):
        while not self.closed.is_set():
            wanted = self.wanted()
            if not wanted:
                self.closed.wait(0.5)
                continue
            try:
                with connect(
                    "wss://ws-subscriptions-clob.polymarket.com/ws/market",
                    open_timeout=10,
                    close_timeout=2,
                ) as ws:
                    ws.send(
                        json.dumps(
                            {
                                "assets_ids": sorted(wanted),
                                "type": "market",
                                "custom_feature_enabled": True,
                            }
                        )
                    )
                    # WebSocket supplies authoritative initial books before incremental changes.
                    ping = connected = time.monotonic()
                    while (
                        not self.closed.is_set()
                        and self.wanted() == wanted
                        and time.monotonic() - connected < 25
                    ):
                        try:
                            raw = ws.recv(timeout=0.5)
                        except TimeoutError:
                            raw = None
                        if raw and raw != "PONG":
                            events = json.loads(raw)
                            for event in events if isinstance(events, list) else [events]:
                                self.ingest(event)
                            if self.last_error:
                                logging.getLogger(__name__).info(
                                    "Public depth connection recovered"
                                )
                                self.last_error = None
                        if time.monotonic() - ping >= 8:
                            ws.send("PING")
                            ping = time.monotonic()
            except Exception as exc:
                self.invalidate()
                if self.last_error != type(exc).__name__:
                    self.last_error = type(exc).__name__
                    logging.getLogger(__name__).warning(
                        "Public depth disconnected: %s", self.last_error
                    )
                self.closed.wait(2)
            finally:
                self.invalidate()
                with self.lock:
                    self.books = {t: b for t, b in self.books.items() if t in self.wanted()}

    def start(self, port=8766):
        feed = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.headers.get("Origin"):
                    self.send_error(403)
                    return
                try:
                    url = urlparse(self.path)
                    if url.path != "/depth":
                        self.send_error(404)
                        return
                    tokens = parse_qs(url.query).get("token", [])
                    body = json.dumps(feed.view(tokens)).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (ValueError, KeyError):
                    self.send_error(400)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.closed.set()
        self.server.shutdown()
        self.server.server_close()

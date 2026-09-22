"""Private terminal API. Credentials never enter feed projections or responses."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from nice_weather.trading.feed import FeedStore
from nice_weather.trading.market_weather import (
    catalog,
    epoch,
    market_day,
    scoped_history,
    weather_history,
)
from nice_weather.trading.storage import Requests, connect
from nice_weather.trading.us_markets import VENUES, book_url, normalize_book


class Login(BaseModel):
    password: str = Field(max_length=1024)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    venue: str
    mode: str
    kind: str
    payload: dict = Field(default_factory=dict)


def create_app(root: Path, *, password=None, origin=None):
    app = FastAPI(title="Nice Weather Terminal", docs_url=None, redoc_url=None, openapi_url=None)
    feed = FeedStore(root / "feed.sqlite3")
    requests = Requests(root / "requests" / "requests.sqlite3")
    password = (
        password if password is not None else os.environ.get("NICE_WEATHER_TERMINAL_PASSWORD")
    )
    origin = origin or os.environ.get("NICE_WEATHER_TERMINAL_ORIGIN", "http://127.0.0.1:8767")
    issuer = os.environ.get("NICE_WEATHER_ACCESS_ISSUER", "")
    audience = os.environ.get("NICE_WEATHER_ACCESS_AUDIENCE", "")
    access = None
    if issuer or audience:
        from nice_weather.trading.access import AccessIdentity

        access = AccessIdentity(issuer, audience)
    auth_mode = "cloudflare" if access else ("password" if password else "unconfigured")
    signing_key = secrets.token_bytes(32)  # Restart invalidates browser sessions.
    csrf = secrets.token_urlsafe(32)
    attempts = {}

    def session_cookie():
        expiry = str(int(time.time() + 8 * 3600))
        return expiry + "." + hmac.new(signing_key, expiry.encode(), hashlib.sha256).hexdigest()

    def authenticated(cookies):
        try:
            expiry, signature = cookies.get("nw_session", "").split(".")
            return int(expiry) > time.time() and hmac.compare_digest(
                signature, hmac.new(signing_key, expiry.encode(), hashlib.sha256).hexdigest()
            )
        except (ValueError, TypeError):
            return False

    async def identity_expiry(connection):
        if access:
            return await asyncio.to_thread(
                access.expires, connection.headers.get("cf-access-jwt-assertion", "")
            )
        if authenticated(connection.cookies):
            return float(connection.cookies["nw_session"].split(".")[0])
        return 0

    @app.middleware("http")
    async def protect(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.url.path not in {
            "/api/login", "/api/auth",
        }:
            if await identity_expiry(request) <= time.time():
                return Response(status_code=401)
            if request.method != "GET" and (
                request.headers.get("origin") != origin
                or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf)
            ):
                return Response(status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @app.get("/health")
    def health():
        return {"status": "ok", "release": os.environ.get("NICE_WEATHER_CODE_SHA", "development")}

    @app.get("/api/auth")
    def authentication_mode():
        return {"mode": auth_mode}

    @app.post("/api/login")
    def login(body: Login, request: Request, response: Response):
        if access:
            raise HTTPException(403, "Use the existing Cloudflare Access login")
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Origin rejected")
        now = time.monotonic()
        # This is a private single-user endpoint; bound the unauthenticated rate-limit map.
        for key in list(attempts):
            if now - attempts[key][0] > 60:
                attempts.pop(key)
        key = request.client.host if request.client else "unknown"
        started, count = attempts.get(key, (now, 0))
        if count >= 10 or len(attempts) >= 1000:
            raise HTTPException(429, "Try again later")
        attempts[key] = (started, count + 1)
        if not password:
            raise HTTPException(503, "Set the terminal password on the server before use")
        if not secrets.compare_digest(body.password.encode(), password.encode()):
            raise HTTPException(401, "Invalid password")
        response.set_cookie(
            "nw_session",
            session_cookie(),
            httponly=True,
            secure=origin.startswith("https://"),
            samesite="strict",
            max_age=28800,
        )
        return {"csrf": csrf}

    @app.get("/api/session")
    def session():
        return {"csrf": csrf}

    def accounts():
        path = root / "results.sqlite3"
        if not path.exists():
            return []
        with connect(path, readonly=True) as con:
            rows = con.execute(
                "SELECT * FROM runs WHERE mode!='backtest' UNION ALL "
                "SELECT * FROM (SELECT * FROM runs WHERE mode='backtest' "
                "ORDER BY updated DESC LIMIT 20) ORDER BY updated DESC"
            ).fetchall()
        return [
            {
                "account": r["account"],
                "mode": r["mode"],
                "status": r["status"],
                "updated": r["updated"],
                "snapshot": json.loads(r["snapshot"] or "{}"),
                "run_id": r["run_id"],
            }
            for r in rows
        ]

    @app.get("/api/snapshot")
    def snapshot():
        return feed.snapshot() | {
            "accounts": accounts(),
            "live": {
                "enabled": False,
                "status": "not_connected",
                "reason": "Live adapter and account reconciliation acceptance pending",
            },
        }

    @app.get("/api/markets")
    def markets(venue: str):
        try:
            return catalog(feed.path, venue)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/market-day")
    def selected_market_day(venue: str, day: str):
        try:
            return market_day(feed.path, venue, day)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/weather-history")
    def selected_weather(venue: str, day: str):
        try:
            return weather_history(feed.path, venue, day)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/market-quote")
    async def selected_quote(venue: str, day: str, token: str):
        try:
            context = await asyncio.to_thread(market_day, feed.path, venue, day)
            contract = next((c for c in context["contracts"] if c["yes_token_id"] == token), None)
            if contract is None:
                raise ValueError("Token does not belong to the requested venue/market day")
            close = epoch(contract.get("close_time"))
            if (not contract.get("active") or contract.get("closed")
                    or (close is not None and close <= time.time())):
                return {"venue": venue, "day": day, "token": token, "quote": None,
                        "reason": "MARKET_CLOSED"}
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(book_url(contract))
                received = time.time()
                response.raise_for_status()
                quote = normalize_book(venue, response.json(), received)
            return {"venue": venue, "day": day, "token": token,
                    "quote": quote | {"time": received, "source": "public_book"},
                    "reason": None}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise HTTPException(502, "Public market quote unavailable") from exc

    @app.get("/api/history")
    def history(token: str, before: int | None = None,
                venue: str | None = None, day: str | None = None):
        if len(token) > 256:
            raise HTTPException(400, "Invalid token")
        if venue is not None or day is not None:
            if not venue or not day:
                raise HTTPException(400, "venue and day must be supplied together")
            try:
                return scoped_history(feed, venue, day, token, before)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        # History draws the midpoint only; preserve full depth in the capture store.
        return [
            {"seq": row["seq"], "time": row["time"],
             "bids": row.get("bids", [])[:1], "asks": row.get("asks", [])[:1]}
            for row in feed.history(token, before, limit=200)
        ]

    @app.get("/api/requests")
    def receipts():
        with connect(requests.path, readonly=True) as con:
            return [
                dict(r)
                for r in con.execute(
                    "SELECT request_id,account,kind,status,error,created FROM requests "
                    "ORDER BY created DESC LIMIT 30"
                )
            ]

    @app.post("/api/paper/preview")
    def paper_preview(body: Command):
        from nice_weather.trading.paper_execution import VERSION, preview

        if body.mode != "sandbox" or body.venue not in VENUES or body.kind != "order":
            raise HTTPException(400, "Paper order preview only")
        account = "sandbox-" + body.venue + "-knyc"
        unavailable = {"available": False, "reason": "Account worker disconnected",
                       "approximate": True, "execution_model": VERSION}
        path = root / "results.sqlite3"
        if not path.exists():
            return unavailable
        with connect(path, readonly=True) as con:
            con.execute("BEGIN")
            run = con.execute("SELECT * FROM runs WHERE account=?", (account,)).fetchone()
            if not run or run["status"] != "running" or time.time() - run["updated"] > 10:
                return unavailable
            saved = con.execute("SELECT body FROM paper_state WHERE run_id=?",
                                (run["run_id"],)).fetchone()
        if not saved:
            return unavailable
        state = json.loads(saved["body"])
        if state["config"].get("execution_model") != VERSION:
            return unavailable | {"reason": "Paper worker requires market-price-v1 upgrade"}
        return preview(state["config"], state["metadata"], state.get("market_prices", {}),
                       json.loads(run["snapshot"]), body.payload, time.time())

    @app.get("/api/paper/equity")
    def paper_equity(run_id: str, after: int = -1):
        if len(run_id) > 100:
            raise HTTPException(400, "Invalid run ID")
        path = root / "results.sqlite3"
        if not path.exists():
            return {"points": [], "next": None}
        with connect(path, readonly=True) as con:
            if not con.execute(
                "SELECT 1 FROM sqlite_master WHERE name='simulation_equity'"
            ).fetchone():
                return {"points": [], "next": None}
            rows = con.execute("SELECT ts,body FROM simulation_equity WHERE run_id=? AND ts>? "
                               "ORDER BY ts LIMIT 1000", (run_id, after)).fetchall()
        return {"points": [json.loads(r["body"]) for r in rows],
                "next": rows[-1]["ts"] if len(rows) == 1000 else None}

    @app.get("/api/requests/{request_id}")
    def receipt(request_id: str):
        if len(request_id) > 100:
            raise HTTPException(400, "Invalid request ID")
        with connect(requests.path, readonly=True) as con:
            row = con.execute(
                "SELECT request_id,account,mode,kind,status,error,created FROM requests "
                "WHERE request_id=?", (request_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(404, "Request not recorded; absence is not execution confirmation")
        return dict(row)

    @app.post("/api/commands", status_code=202)
    def command(body: Command):
        if body.venue in VENUES and body.mode == "backtest" and body.kind == "backtest":
            start, end = body.payload.get("start"), body.payload.get("end")
            if (
                type(start) not in (int, float)
                or type(end) not in (int, float)
                or not 0 <= start < end <= time.time()
            ):
                raise HTTPException(400, "Invalid replay interval")
            if body.payload.get("strategy", "S1_S2_S3") not in {"S1", "S2", "S3", "S1_S2_S3"}:
                raise HTTPException(400, "Unknown strategy")
            try:
                requests.submit(
                    body.request_id,
                    "backtest-" + body.venue,
                    "backtest",
                    "backtest",
                    body.payload,
                    ttl=86400,
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return {"request_id": body.request_id, "status": "queued"}
        if body.venue not in VENUES or body.mode != "sandbox":
            raise HTTPException(409, "Live execution has not passed adapter acceptance")
        if body.kind not in {"order", "cancel", "close", "start", "stop", "simulation_settings"}:
            raise HTTPException(400, "Unsupported command")
        account = "sandbox-" + body.venue + "-knyc"
        active = next((r for r in accounts() if r["account"] == account), None)
        if not active or active["status"] != "running" or time.time() - active["updated"] > 10:
            raise HTTPException(409, "Account worker disconnected; command not submitted")
        try:
            requests.submit(body.request_id, account, "sandbox", body.kind, body.payload)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"request_id": body.request_id, "status": "queued"}

    @app.websocket("/api/events")
    async def events(ws: WebSocket):
        expiry = await identity_expiry(ws)
        if ws.headers.get("origin") != origin or expiry <= time.time():
            await ws.close(code=1008)
            return
        await ws.accept()
        try:
            cursor = int(ws.query_params.get("cursor", "0"))
            last_account = 0.0
            while True:
                if time.time() >= expiry:
                    await ws.close(code=1008)
                    return
                batch = await asyncio.to_thread(feed.since, cursor)
                if batch:
                    cursor = batch[-1]["seq"]
                    await asyncio.wait_for(ws.send_json({"events": batch, "cursor": cursor}), 5)
                if time.monotonic() - last_account > 1:
                    await asyncio.wait_for(
                        ws.send_json(
                            {
                                "accounts": await asyncio.to_thread(accounts),
                                "server_time": time.time(),
                            }
                        ),
                        5,
                    )
                    last_account = time.monotonic()
                await asyncio.sleep(0.1)
        except (WebSocketDisconnect, TimeoutError, ValueError, RuntimeError):
            return

    static = Path(__file__).resolve().parents[1] / "terminal_dist"
    if (static / "assets").exists():
        app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

    @app.get("/")
    @app.get("/terminal")
    def index():
        if not (static / "index.html").exists():
            raise HTTPException(503, "Build frontend/terminal before starting this service")
        return FileResponse(static / "index.html")

    return app


def main():
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    uvicorn.run(create_app(args.root), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()

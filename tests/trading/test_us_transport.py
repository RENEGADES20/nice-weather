import asyncio
import base64

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from nice_weather.trading.us_transport import USRest, order_body


def contract(venue):
    return {
        "venue": venue,
        "condition_id": "KXHIGHNY-26SEP19-B70.5"
        if venue == "kalshi"
        else "tc-temp-nychigh-2026-09-19-69to70f",
        "tick_size": ".01",
        "quantity_step": ".01",
        "minimum_order_size": 0.01,
        "parse_status": "parsed",
        "fee_known": True,
        "fee_rate": 0.07,
        "fee_exponent": 1,
        "fee_rounding": "ceil_cent",
    }


@pytest.mark.parametrize("venue", ["kalshi", "poly_us"])
def test_signatures_timeout_and_durable_no_retry(tmp_path, venue):
    key = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if venue == "kalshi"
        else ed25519.Ed25519PrivateKey.generate()
    )
    secret = (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        if venue == "kalshi"
        else base64.b64encode(key.private_bytes_raw()).decode()
    )
    calls = []

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("fixture timeout", request=request)

    async def run():
        path = tmp_path / "attempts.sqlite3"
        client = USRest(
            venue,
            f"live-{venue}-test",
            "test-key",
            secret,
            path,
            transport=httpx.MockTransport(timeout),
        )
        headers = client.headers("GET", "/sample?cursor=ignored", 123)
        signed = base64.b64decode(
            headers["KALSHI-ACCESS-SIGNATURE" if venue == "kalshi" else "X-PM-Signature"]
        )
        if venue == "kalshi":
            key.public_key().verify(
                signed,
                b"123GET/sample",
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH
                ),
                hashes.SHA256(),
            )
        else:
            key.public_key().verify(signed, b"123GET/sample")
        order = {"side": "BUY", "outcome": "NO", "price": 0.4, "quantity": 1}
        with pytest.raises(ValueError, match="disabled"):
            await client.submit("one", contract(venue), order)
        assert calls == []
        client.gate = lambda *_: True
        assert (await client.submit("one", contract(venue), order))["status"] == "unknown"
        assert len(calls) == 1
        await client.close()
        recovered = USRest(
            venue,
            f"live-{venue}-test",
            "test-key",
            secret,
            path,
            gate=lambda *_: True,
            transport=httpx.MockTransport(timeout),
        )
        assert (await recovered.submit("one", contract(venue), order))["status"] == "unknown"
        with pytest.raises(ValueError, match="unresolved"):
            await recovered.submit("two", contract(venue), order)
        with pytest.raises(ValueError, match="reused"):
            await recovered.submit("one", contract(venue), order | {"quantity": 2})
        assert len(calls) == 1
        await recovered.close()

    asyncio.run(run())


def test_yes_no_order_mapping():
    for side in ("BUY", "SELL"):
        for outcome in ("YES", "NO"):
            order = {"side": side, "outcome": outcome, "quantity": 1, "price": 0.35, "owner": "S1"}
            k = order_body("kalshi", "stable", contract("kalshi"), order)
            assert k["price"] == ("0.3500" if outcome == "YES" else "0.6500")
            assert k["side"] == ("bid" if (side == "BUY") == (outcome == "YES") else "ask")
            assert k["reduce_only"] == (side == "SELL")
            p = order_body("poly_us", "stable", contract("poly_us"), order)
            assert p["outcomeSide"] == "OUTCOME_SIDE_" + outcome
            assert p["price"]["value"] == ("0.35" if outcome == "YES" else "0.65")
            assert p["action"] == "ORDER_ACTION_" + side
            assert p["manualOrderIndicator"] == "MANUAL_ORDER_INDICATOR_AUTOMATIC"


def make_poly(tmp_path, handler):
    key = ed25519.Ed25519PrivateKey.generate()
    return USRest(
        "poly_us",
        "live-poly_us-test",
        "fixture-key",
        base64.b64encode(key.private_bytes_raw()).decode(),
        tmp_path / "attempts.sqlite3",
        gate=lambda *_: True,
        transport=httpx.MockTransport(handler),
    )


def test_response_decimal_facts_survive_read_and_durable_receipt(tmp_path):
    raw = b'{"id":"exchange-1","amount":1.0000000000000001,"count":1,"eof":true}'

    async def run():
        client = make_poly(tmp_path, lambda _: httpx.Response(200, content=raw))
        try:
            result = await client.read("/account/balances")
            assert result["amount"] == "1.0000000000000001"
            assert result["count"] == 1 and result["eof"] is True
            result = await client.submit("exact", contract("poly_us"), {
                "outcome": "YES", "side": "BUY", "price": ".35", "quantity": "1",
            })
            assert result["status"] == "accepted"
            assert result["response"]["amount"] == "1.0000000000000001"
        finally:
            await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (201, {"id": "exchange-1"}, "accepted"),
        (200, {"id": ""}, "unknown"),
        (400, {"error": "invalid"}, "rejected"),
        (429, {}, "unknown"),
        (503, {}, "unknown"),
        (307, {}, "unknown"),
    ],
)
def test_response_classification_never_resends(tmp_path, status, body, expected):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=body, headers={"Location": "https://example.com"})

    async def run():
        client = make_poly(tmp_path, handle)
        order = {"side": "BUY", "outcome": "YES", "quantity": 1, "price": 0.35}
        with pytest.raises(ValueError, match="no-trade"):
            await client.submit("bad", contract("poly_us") | {"parse_status": "ambiguous"}, order)
        assert not calls
        first = await client.submit("same", contract("poly_us"), order)
        assert first["status"] == expected
        assert (await client.submit("same", contract("poly_us"), order))["status"] == expected
        assert len(calls) == 1  # Including redirects and venue rate limits.
        await client.close()

    asyncio.run(run())


def test_paginated_read_preserves_signed_positions_and_unknown_orders(tmp_path):
    cursors = []

    def handle(request):
        if request.method == "POST":
            raise httpx.ReadTimeout("response lost", request=request)
        if request.url.path.endswith("/balances"):
            return httpx.Response(200, json={"balances": [{"currency": "USD", "buyingPower": "7"}]})
        if request.url.path.endswith("/open"):
            return httpx.Response(200, json={"orders": []})
        cursor = request.url.params.get("cursor")
        cursors.append(cursor)
        payload = (
            {"positions": {"market-a": {"netPosition": "-2.00"}}, "nextCursor": "next"}
            if cursor is None
            else {"positions": {"market-b": {"netPosition": "1.00"}}, "eof": True}
        )
        return httpx.Response(200, json=payload)

    async def run():
        client = make_poly(tmp_path, handle)
        await client.submit(
            "lost",
            contract("poly_us"),
            {"side": "BUY", "outcome": "YES", "quantity": 1, "price": 0.35},
        )
        snapshot = await client.account_snapshot()
        assert snapshot["reconciled"] is False
        assert [p["netPosition"] for p in snapshot["positions"]] == ["-2.00", "1.00"]
        assert cursors == [None, "next"]
        assert client.attempt("lost")["status"] == "unknown"
        await client.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "response",
    [
        {"positions": {"a": {"marketSlug": "b"}}, "eof": True},
        {"positions": {"a": None}, "eof": True},
        {"positions": {}, "nextCursor": "same"},
    ],
)
def test_incomplete_account_read_fails(tmp_path, response):
    async def run():
        client = make_poly(tmp_path, lambda _: httpx.Response(200, json=response))
        with pytest.raises(ValueError):
            await client._pages("/portfolio/positions", "positions")
        await client.close()

    asyncio.run(run())


@pytest.mark.parametrize("field,value", [("price", "0.35001"), ("quantity", "1.001")])
def test_kalshi_never_rounds_wire_instruction(field, value):
    c = contract("kalshi") | {"tick_size": ".00001", "quantity_step": ".001"}
    order = {"side": "BUY", "outcome": "YES", "quantity": 1, "price": 0.35, field: value}
    with pytest.raises(ValueError, match="precision"):
        order_body("kalshi", "rounding", c, order)


def test_known_order_can_be_cancelled_while_submission_is_unknown(tmp_path):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(200, json={})
        raise httpx.ReadTimeout("response lost", request=request)

    async def run():
        client = make_poly(tmp_path, handle)
        order = {"side": "BUY", "outcome": "YES", "quantity": 1, "price": 0.35}
        await client.submit("lost", contract("poly_us"), order)
        # Rules may become ambiguous after an earlier accepted order.
        c = contract("poly_us") | {"parse_status": "ambiguous"}
        assert (await client.cancel("cancel-one", c, "known-order"))["status"] == "accepted"
        assert (await client.cancel("cancel-one", c, "known-order"))["status"] == "accepted"
        assert len(calls) == 2
        assert calls[-1].url.path == "/v1/order/known-order/cancel"
        # A cancel acknowledgement does not resolve the other uncertain submission.
        with pytest.raises(ValueError, match="unresolved"):
            await client.submit("another", contract("poly_us"), order)
        await client.close()

    asyncio.run(run())

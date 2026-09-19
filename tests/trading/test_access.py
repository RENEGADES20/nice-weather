import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nice_weather.trading.access import AccessIdentity
from nice_weather.trading.api import create_app

ISSUER = "https://test-team.cloudflareaccess.com"
AUDIENCE = "test-application"
ORIGIN = "https://testserver"


@pytest.fixture
def identity(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setenv("NICE_WEATHER_ACCESS_ISSUER", ISSUER)
    monkeypatch.setenv("NICE_WEATHER_ACCESS_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(jwt.PyJWKClient, "get_signing_key_from_jwt",
                        lambda *_: SimpleNamespace(key=key.public_key()))

    def token(**changes):
        claims = {"iss": ISSUER, "aud": [AUDIENCE], "sub": "user", "type": "app",
                  "iat": int(time.time()) - 10, "exp": int(time.time()) + 60}
        claims.update(changes)
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"})

    return token


def test_access_reuses_identity_and_rejects_spoofing(identity, tmp_path):
    with TestClient(create_app(tmp_path, password="unused", origin=ORIGIN)) as client:
        assert client.get("/api/auth").json() == {"mode": "cloudflare"}
        assert client.get("/api/snapshot").status_code == 401
        assert client.get("/api/session", headers={
            "Cf-Access-Authenticated-User-Email": "forged@example.com",
        }).status_code == 401
        for token in ["forged", identity(aud="other-app"), identity(iss="https://other.invalid"),
                      identity(exp=1), identity(type="service"), identity(iat=time.time() + 500)]:
            assert client.get("/api/session", headers={
                "Cf-Access-Jwt-Assertion": token,
            }).status_code == 401
        assert client.post("/api/login", json={"password": "unused"},
                           headers={"Origin": ORIGIN}).status_code == 403
        headers = {"Cf-Access-Jwt-Assertion": identity()}
        response = client.get("/api/session", headers=headers)
        assert response.status_code == 200
        assert "set-cookie" not in response.headers  # No second authentication credential.
        body = {"request_id": "access-test", "venue": "kalshi", "mode": "backtest",
                "kind": "backtest", "payload": {"start": 1, "end": 2}}
        assert client.post("/api/commands", headers=headers, json=body).status_code == 403
        headers |= {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf"]}
        assert client.post("/api/commands", headers=headers, json=body).status_code == 202
        assert client.post("/api/commands", headers=headers | {"Origin": "https://evil.invalid"},
                           json=body).status_code == 403
        with client.websocket_connect("/api/events", headers=headers) as ws:
            assert "accounts" in ws.receive_json()
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(
            "/api/events", headers={"Origin": ORIGIN}
        ):
            pass


def test_invalid_signature_and_unavailable_keys_fail_closed(identity, monkeypatch):
    verifier = AccessIdentity(ISSUER, AUDIENCE)
    token = identity()
    foreign = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(verifier.keys, "get_signing_key_from_jwt",
                        lambda *_: SimpleNamespace(key=foreign.public_key()))
    assert verifier.expires(token) == 0

    def unavailable(*_):
        raise jwt.PyJWKClientConnectionError("offline")

    monkeypatch.setattr(verifier.keys, "get_signing_key_from_jwt", unavailable)
    assert verifier.expires(token) == 0


def test_websocket_closes_when_identity_expires(identity, tmp_path):
    with TestClient(create_app(tmp_path, origin=ORIGIN)) as client:
        headers = {"Origin": ORIGIN, "Cf-Access-Jwt-Assertion": identity(exp=time.time() + 2)}
        with client.websocket_connect("/api/events", headers=headers) as ws:
            assert "accounts" in ws.receive_json()
            with pytest.raises(WebSocketDisconnect):
                for _ in range(5):
                    ws.receive_json()

@pytest.mark.parametrize("issuer,audience", [
    ("", AUDIENCE), (ISSUER, ""), ("http://test.cloudflareaccess.com", AUDIENCE),
    ("https://test.cloudflareaccess.com.attacker.invalid", AUDIENCE),
    (ISSUER + "/unexpected", AUDIENCE),
])
def test_partial_or_invalid_configuration_rejected(issuer, audience):
    with pytest.raises(ValueError):
        AccessIdentity(issuer, audience)

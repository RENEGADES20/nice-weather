"""Validate the existing Cloudflare Access identity at the private origin."""

from urllib.parse import urlsplit

import jwt


class AccessIdentity:
    def __init__(self, issuer: str, audience: str):
        parsed = urlsplit(issuer)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".cloudflareaccess.com")
            or parsed.username or parsed.password or parsed.port
            or parsed.path or parsed.query or parsed.fragment
            or not audience
        ):
            raise ValueError("Configure the exact Access issuer and application audience")
        self.issuer, self.audience = issuer, audience
        self.keys = jwt.PyJWKClient(issuer + "/cdn-cgi/access/certs", timeout=3, lifespan=300)

    def expires(self, assertion: str) -> float:
        if not assertion or len(assertion) > 16384:
            return 0
        try:
            key = self.keys.get_signing_key_from_jwt(assertion).key
            claims = jwt.decode(
                assertion, key, algorithms=["RS256"], issuer=self.issuer,
                audience=self.audience, options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            return float(claims["exp"]) if claims.get("type") == "app" else 0
        except (jwt.PyJWTError, ValueError, TypeError, OSError):
            return 0

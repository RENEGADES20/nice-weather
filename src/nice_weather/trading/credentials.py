"""Read existing server-side credential files. Never return secrets in diagnostics."""

import re
from pathlib import Path


def read_credentials(path: Path, venue: str):
    if venue not in {"kalshi", "poly_us"}:
        raise ValueError("Unsupported credential venue")
    text = path.read_text(encoding="utf-8-sig")
    identifiers = re.findall(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", text)
    if len(identifiers) != 1:
        raise ValueError("Credential file requires exactly one API key identifier")
    if venue == "kalshi":
        secrets = re.findall(
            r"-----BEGIN (?:RSA )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA )?PRIVATE KEY-----", text
        )
    else:
        secrets = re.findall(
            r"(?im)^\s*(?:SECRET_KEY|SECRET|PRIVATE_KEY)\s*[:=]\s*([A-Za-z0-9+/=]+)\s*$", text
        )
    if len(secrets) != 1:
        raise ValueError("Credential file requires exactly one private key")
    return identifiers[0], secrets[0]

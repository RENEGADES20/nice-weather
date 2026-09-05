from __future__ import annotations

import base64
import gzip
import json
from importlib.resources import files
from time import time
from typing import Any

import streamlit.components.v1 as components

_DIST = files("nice_weather").joinpath("trading_chart_dist")
_component = components.declare_component("nice_weather_trading_chart", path=str(_DIST))


def _chart_transport(payload: dict[str, Any]) -> dict[str, Any]:
    # Keep every tick and its provenance; compress only the transport representation.
    encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    return {
        "gzip": base64.b64encode(gzip.compress(encoded, compresslevel=1, mtime=0)).decode(),
        "sentAt": time(),
    }


def trading_chart(payload: dict[str, Any], *, key: str) -> Any:
    return _component(payload=_chart_transport(payload), height=930, key=key, default=None)


def trading_chart_feed(payload: dict[str, Any], *, key: str) -> Any:
    return _component(payload=_chart_transport(payload), height=0, key=key, default=None)

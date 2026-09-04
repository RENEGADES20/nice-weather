from __future__ import annotations

from importlib.resources import files
from typing import Any

import streamlit.components.v1 as components

_DIST = files("nice_weather").joinpath("trading_chart_dist")
_component = components.declare_component("nice_weather_trading_chart", path=str(_DIST))


def trading_chart(payload: dict[str, Any], *, key: str) -> Any:
    return _component(payload=payload, height=790, key=key, default=None)


def trading_chart_feed(payload: dict[str, Any], *, key: str) -> Any:
    return _component(payload=payload, height=0, key=key, default=None)

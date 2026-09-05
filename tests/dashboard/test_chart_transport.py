import base64
import gzip
import json

import pytest

from nice_weather.trading_chart import _chart_transport


def test_large_chart_transport_preserves_every_tick_and_provenance():
    payload = {
        "mode": "full",
        "sequence": 7,
        "points": [
            {
                "time": 1_788_580_800 + index / 10,
                "value": None if index % 13 == 0 else (index % 5) / 100,
                "priceSource": "CLOB mid",
                "binId": "bin-79",
                "receivedAt": "2026-09-05T12:00:00+00:00",
            }
            for index in range(70_000)
        ],
    }
    transport = _chart_transport(payload)
    assert json.loads(gzip.decompress(base64.b64decode(transport["gzip"]))) == payload
    assert len(transport["gzip"]) < len(json.dumps(payload)) / 10
    assert _chart_transport(payload)["gzip"] == transport["gzip"]
    with pytest.raises(ValueError):
        _chart_transport({"value": float("nan")})

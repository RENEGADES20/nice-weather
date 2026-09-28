from datetime import UTC, datetime

from nice_weather.trading import hrrr


def test_hrrr_fetch_does_not_persist_index_or_grib(monkeypatch):
    class Response:
        status_code = 206
        headers = {"Content-Range": "bytes 0-3/4"}
        text = "1:0:d=20260928:TMP:2 m above ground:\n2:4:d=20260928:other:"

        def raise_for_status(self):
            pass

        def read(self):
            return b"grib"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def get(self, _):
            return Response()

        def stream(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(hrrr.httpx, "Client", Client)
    monkeypatch.setattr(hrrr, "point", lambda *_: {"temperature_f": 70.0})
    result = hrrr.collect(datetime(2026, 9, 28, tzinfo=UTC))
    assert len(result["points"]) == 18
    assert all("index_capture" not in point for point in result["points"])

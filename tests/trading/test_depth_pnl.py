from datetime import UTC, datetime

import pytest

from nice_weather.trading.depth import DepthFeed
from nice_weather.trading.metrics import pnl_view


def test_depth_absolute_deltas_stale_timestamp_and_subscription_leases(monkeypatch):
    feed = DepthFeed()
    feed.allowed = {"one", "two"}
    feed.ingest(
        {
            "event_type": "book",
            "asset_id": "one",
            "timestamp": "100",
            "bids": [{"price": ".390", "size": "5"}],
            "asks": [{"price": ".400", "size": "2"}, {"price": ".41", "size": "3"}],
        }
    )
    feed.ingest(
        {
            "event_type": "price_change",
            "timestamp": "101",
            "price_changes": [{"asset_id": "one", "side": "SELL", "price": ".40", "size": "0"}],
        }
    )
    assert feed.book("one")["asks"] == [["0.41", "3"]] or feed.book("one")["asks"] == [
        ("0.41", "3")
    ]
    feed.ingest(
        {
            "event_type": "price_change",
            "timestamp": "99",
            "price_changes": [{"asset_id": "one", "side": "SELL", "price": ".40", "size": "9"}],
        }
    )
    assert len(feed.book("one")["asks"]) == 1
    feed.view(["one"])
    feed.required = {"two"}
    assert feed.wanted() == {"one", "two"}
    feed.leases["one"] = 0
    assert feed.wanted() == {"two"}
    with pytest.raises(ValueError):
        feed.view(["unknown"])
    monkeypatch.setattr("nice_weather.trading.depth.time.time_ns", lambda: 10**30)
    assert not feed.book("one")["valid"]


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-03-08T05:00:00+00:00", "2026-03-09T04:00:00+00:00"),
        ("2026-11-01T04:00:00+00:00", "2026-11-02T05:00:00+00:00"),
    ],
)
def test_ny_calendar_dst_first_day_and_gap_not_shifted(start, end):
    a = int(datetime.fromisoformat(start).timestamp() * 1e9)
    b = int(datetime.fromisoformat(end).timestamp() * 1e9)
    samples = [
        {"ts": a, "equity": 100, "realized": 0},
        {"ts": b - 60_000_000_000, "equity": 105, "realized": 2},
        {"ts": b, "equity": 106, "realized": 3},
        {"ts": b + 3 * 86400_000_000_000, "equity": 110, "realized": 3},
    ]
    result = pnl_view(samples, 100, [], a)
    assert result["days"][0]["pnl"] == 5
    assert result["days"][0]["realized_change"] == 2
    assert result["days"][0]["unrealized_change"] == 3
    assert result["days"][-1]["pnl"] is None
    assert any(p["value"] is None for p in result["points"])
    assert datetime.fromtimestamp(a / 1e9, UTC).hour in {4, 5}


def test_subscription_changes_keep_the_existing_connection(monkeypatch):
    import json
    from contextlib import contextmanager

    feed = DepthFeed()
    feed.allowed = {"one", "two"}
    feed.required = {"one"}
    calls, sent, observed = [], [], []
    clock = [0]
    monkeypatch.setattr("nice_weather.trading.depth.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(feed, "snapshots", lambda tokens: [])

    class Socket:
        def send(self, message):
            sent.append(message)

        def recv(self, timeout):
            clock[0] += 1
            if clock[0] == 2:
                feed.required.add("two")
            if clock[0] == 5:
                feed.required.remove("one")
            if clock[0] >= 40:
                feed.closed.set()
            observed.append(clock[0])
            return "PONG"

    @contextmanager
    def connection(*args, **kwargs):
        calls.append(1)
        yield Socket()

    monkeypatch.setattr("nice_weather.trading.depth.connect", connection)
    feed.run()
    assert len(calls) == 1 and max(observed) == 40
    updates = [json.loads(s) for s in sent if s != "PING"]
    assert any(u.get("operation") == "subscribe" and u["assets_ids"] == ["two"] for u in updates)
    assert any(u.get("operation") == "unsubscribe" and u["assets_ids"] == ["one"] for u in updates)


def test_complete_empty_side_is_distinct_from_missing_snapshot():
    feed = DepthFeed()
    feed.allowed = {"one"}
    row = {
        "event_type": "book",
        "asset_id": "one",
        "timestamp": "100",
        "bids": [],
        "asks": [{"price": ".4", "size": "5"}],
    }
    feed.ingest(row)
    assert feed.book("one")["valid"]
    del row["bids"]
    feed.ingest(row)
    assert not feed.book("one")["valid"]

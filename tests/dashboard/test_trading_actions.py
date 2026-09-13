import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from nice_weather.trading.storage import Requests, Results


def show_trading(root):
    from pathlib import Path

    from nice_weather.trading.ui import trading

    trading(Path(root))


@pytest.mark.parametrize("action", ["replace", "close"])
def test_action_requires_review_and_duplicate_submit_is_idempotent(tmp_path, monkeypatch, action):
    from nice_weather.trading import ui

    monkeypatch.setattr(ui, "time", SimpleNamespace(time=lambda: checked_at))
    checked_at = time.time()
    evidence = (
        Path(__file__).parents[2] / "docs/acceptance/nautilus-2026-09-12/account-and-runs.json"
    )
    snapshot = json.loads(evidence.read_text(encoding="utf-8"))["sandbox"]["snapshot"]
    results = Results(tmp_path / "results.sqlite3")
    results.create("preview-test", "sandbox-test", "sandbox", {})
    seq = results.append("preview-test", "fixture", {"kind": "clock", "ts": snapshot["ts"]})
    results.checkpoint("preview-test", seq, snapshot)
    requests = Requests(tmp_path / "requests/requests.sqlite3")
    app = AppTest.from_function(show_trading, args=(str(tmp_path),), default_timeout=30).run()
    assert not app.exception

    def field(kind, label):
        return next(item for item in getattr(app, kind) if item.label == label)

    field("selectbox", "Position / order action").select(action)
    field("number_input", "Target total / exit shares (0 = full exit)").set_value(5)
    field("number_input", "Exit / replacement limit").set_value(0.381)
    field("button", "Preview action").click().run()
    assert not app.exception
    assert not requests.pending("sandbox-test", "sandbox")
    assert any("Estimated taker fee" in item.value for item in app.caption)
    field("button", "Submit reviewed action").click().run()
    field("button", "Submit reviewed action").click().run()
    assert not app.exception
    queued = requests.pending("sandbox-test", "sandbox")
    assert len(queued) == 1
    assert queued[0]["kind"] == action
    assert json.loads(queued[0]["payload"])["price"] == 0.381

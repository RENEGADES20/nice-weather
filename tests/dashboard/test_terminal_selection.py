from streamlit.testing.v1 import AppTest


def show_terminal():
    from pathlib import Path

    from nice_weather.trading.terminal import terminal

    terminal(Path("."), Path("missing.sqlite3"), "Paper", "sandbox-test")


def test_default_contract_is_nearest_day_and_survives_metadata_reorder(monkeypatch):
    import json
    import time

    from nice_weather.trading import terminal

    markets = [
        {
            "token": "later",
            "date": "2099-02-02",
            "bin": "80-81",
            "outcome": "YES",
            "condition": "b",
        },
        {"token": "near", "date": "2099-02-01", "bin": "80-81", "outcome": "YES", "condition": "a"},
    ]

    def rows(_path, sql, _params):
        if "FROM runs" in sql:
            return [
                {
                    "run_id": "one",
                    "snapshot": json.dumps({"markets": markets}),
                    "config": "{}",
                    "updated": time.time(),
                    "status": "running",
                }
            ]
        return []

    monkeypatch.setattr(terminal, "rows", rows)
    monkeypatch.setattr(terminal, "equity_history", lambda *_: [])
    monkeypatch.setattr(terminal, "_component", lambda **_: None)
    app = AppTest.from_function(show_terminal).run()
    assert not app.exception
    assert app.session_state["terminal-token-sandbox-test"] == "near"
    app.session_state["terminal-token-sandbox-test"] = "later"
    markets.reverse()
    app.run()
    assert not app.exception
    assert app.session_state["terminal-token-sandbox-test"] == "later"

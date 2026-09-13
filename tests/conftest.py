from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixture_manifest() -> Path:
    return Path(__file__).parent / "fixtures" / "nyc_klga" / "2026-08-24T0043Z" / "manifest.json"


@pytest.fixture
def seed_yes_token(fixture_manifest):
    def seed(database):
        import sqlite3

        from nice_weather.runner import run_fixture_once

        run_fixture_once(fixture_manifest, database)
        with sqlite3.connect(database) as con:
            con.execute("UPDATE contract_bins SET yes_token_id='token' WHERE ordinal=0")

    return seed

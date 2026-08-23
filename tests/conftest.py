from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def fixture_manifest() -> Path:
    return Path(__file__).parent / "fixtures" / "nyc_klga" / "2026-08-24T0043Z" / "manifest.json"

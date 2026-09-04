from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("#1f7a68", "#2e7d62", "#0f5132", "#064e3b")


def test_active_dashboard_assets_do_not_use_dark_green_tokens() -> None:
    sources = [
        ROOT / "src" / "nice_weather" / "dashboard.py",
        ROOT / "frontend" / "trading-chart" / "src" / "redesign.ts",
        ROOT / "frontend" / "trading-chart" / "src" / "redesign.css",
    ]
    dist = ROOT / "src" / "nice_weather" / "trading_chart_dist"
    index = (dist / "index.html").read_text(encoding="utf-8")
    sources.extend(dist / match for match in re.findall(r"assets/[A-Za-z0-9_.-]+", index))
    violations = {
        str(path.relative_to(ROOT)): [
            token for token in FORBIDDEN if token in path.read_text(encoding="utf-8").lower()
        ]
        for path in sources
        if path.exists()
    }
    assert not {path: tokens for path, tokens in violations.items() if tokens}

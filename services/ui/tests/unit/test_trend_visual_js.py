"""Regression test for T-048: ``trendVisual`` must match the API's trend vocabulary.

Failing-first proof for the verify-then-fix item in WP-08 (plan §5 WP-08.2): the
API's ``economy_phase.task_creation_trend`` field emits ``increasing`` /
``decreasing`` / ``stable`` (see ``ui_service.services.metrics.compute_economy_phase``),
but ``shared.js``'s ``trendVisual`` checked for ``growing`` / ``declining`` — a
vocabulary mismatch that silently degraded every increasing/decreasing trend to
the neutral (stable) arrow and color.

This loads the real ``shared.js`` asset in a headless browser (no live server
required — the function under test is pure) and calls ``ATE.trendVisual``
directly, so it exercises the actual shipped asset rather than a reimplementation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.unit

SHARED_JS_PATH = Path(__file__).resolve().parents[2] / "data" / "web" / "assets" / "shared.js"


def _trend_visual(trend: str) -> dict[str, object]:
    """Load shared.js in a headless page and call ATE.trendVisual(trend)."""
    script = SHARED_JS_PATH.read_text()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            page.add_script_tag(content=script)
            return page.evaluate("(trend) => window.ATE.trendVisual(trend)", trend)
        finally:
            browser.close()


def test_trend_visual_matches_increasing_api_value() -> None:
    """The API's 'increasing' value must render as the up arrow, not neutral."""
    result = _trend_visual("increasing")
    assert result["up"] is True
    assert result["arrow"] == "↑"


def test_trend_visual_matches_decreasing_api_value() -> None:
    """The API's 'decreasing' value must render as the down arrow, not neutral."""
    result = _trend_visual("decreasing")
    assert result["up"] is False
    assert result["arrow"] == "↓"


def test_trend_visual_stable_is_neutral() -> None:
    """The API's 'stable' value renders as the neutral arrow."""
    result = _trend_visual("stable")
    assert result["up"] is None
    assert result["arrow"] == "→"

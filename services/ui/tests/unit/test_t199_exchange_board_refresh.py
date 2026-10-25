"""Exchange-board polls update charts without replaying entrance animations."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

pytestmark = pytest.mark.unit
_ASSETS = Path(__file__).resolve().parents[2] / "data/web/assets"


def test_poll_preserves_chart_nodes_and_updates_live_values() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="board-grid"></div>')
            page.add_script_tag(content=(_ASSETS / "shared.js").read_text())
            page.evaluate("""() => {
                ATE.fetchMetrics = ATE.fetchAgents = ATE.fetchGDPHistory =
                    ATE.fetchSparklines = async () => {};
                ATE.S.gdpHistory = [1, 2];
                ATE.startMetricsPolling = callback => { window.refreshBoard = callback; };
                window.setInterval = () => 1;
            }""")
            page.add_script_tag(content=(_ASSETS / "landing.js").read_text())
            page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
            page.wait_for_function("typeof window.refreshBoard === 'function'")
            result = page.evaluate("""async () => {
                const cell = document.querySelector('.board-cell');
                const bar = cell.querySelector('.bar');
                await window.refreshBoard();
                const unchanged = cell === document.querySelector('.board-cell') &&
                    bar === cell.querySelector('.bar');
                ATE.S.gdp.total = 42;
                ATE.S.gdpHistory = [0.3, 0.4];
                await window.refreshBoard();
                return {
                    unchanged,
                    retained: bar === document.querySelector('.board-cell .bar'),
                    value: cell.querySelector('.cell-value').textContent,
                    height: bar.style.height
                };
            }""")
            assert result["unchanged"]
            assert result["retained"]
            assert result["value"] == "42 ©"
            assert result["height"] == "75%"
        finally:
            browser.close()


def test_empty_history_has_finite_zero_height_bars() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content('<div id="board-grid"></div>')
            page.add_script_tag(content=(_ASSETS / "shared.js").read_text())
            page.evaluate("""() => {
                ATE.fetchMetrics = ATE.fetchAgents = ATE.fetchGDPHistory =
                    ATE.fetchSparklines = async () => {};
                ATE.startMetricsPolling = callback => { window.refreshBoard = callback; };
                window.setInterval = () => 1;
            }""")
            page.add_script_tag(content=(_ASSETS / "landing.js").read_text())
            page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
            page.wait_for_function("typeof window.refreshBoard === 'function'")
            heights = page.locator(".cell-spark .bar").evaluate_all(
                "bars => bars.map(bar => bar.style.height)"
            )
            assert heights
            assert set(heights) == {"0%"}
        finally:
            browser.close()

"""E2E tests for the quarterly-report page (WP-08 item 8, T-093).

No Playwright specs existed for this page before WP-08. These are the
minimal "renders with live data" acceptance tests the WP-08 brief asks for
when no pre-existing spec is available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.mark.e2e
def test_quarterly_report_page_loads_current_quarter(e2e_page: Page) -> None:
    """With no ?quarter, the page defaults to the current quarter."""
    e2e_page.goto(f"{e2e_page.base_url}/quarterly-report.html")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")

    assert e2e_page.title() != ""
    e2e_page.wait_for_function(
        "document.getElementById('qr-quarter-label').textContent !== '—'",
        timeout=5000,
    )
    label = e2e_page.locator("#qr-quarter-label").text_content()
    assert label and label.strip() != ""


@pytest.mark.e2e
def test_quarterly_report_page_explicit_quarter_renders_metrics(e2e_page: Page) -> None:
    """An explicit ?quarter=2026-Q1 (seeded data exists) renders GDP/task metrics."""
    e2e_page.goto(f"{e2e_page.base_url}/quarterly-report.html?quarter=2026-Q1")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")

    e2e_page.wait_for_selector(".metric-grid .metric-cell", timeout=5000)
    cells = e2e_page.locator(".metric-grid .metric-cell")
    assert cells.count() > 0

    label = e2e_page.locator("#qr-quarter-label").text_content()
    assert label and label.strip() == "2026-Q1"


@pytest.mark.e2e
def test_quarterly_report_page_prev_next_navigation(e2e_page: Page) -> None:
    """Prev/Next buttons shift the displayed quarter label."""
    e2e_page.goto(f"{e2e_page.base_url}/quarterly-report.html?quarter=2026-Q1")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    e2e_page.wait_for_function(
        "document.getElementById('qr-quarter-label').textContent === '2026-Q1'",
        timeout=5000,
    )

    e2e_page.locator("#qr-next").click()
    e2e_page.wait_for_function(
        "document.getElementById('qr-quarter-label').textContent === '2026-Q2'",
        timeout=5000,
    )

    e2e_page.locator("#qr-prev").click()
    e2e_page.locator("#qr-prev").click()
    e2e_page.wait_for_function(
        "document.getElementById('qr-quarter-label').textContent === '2025-Q4'",
        timeout=5000,
    )


@pytest.mark.e2e
def test_quarterly_report_page_no_data_quarter_shows_message(e2e_page: Page) -> None:
    """A quarter with no economy data shows a not-found message instead of crashing."""
    e2e_page.goto(f"{e2e_page.base_url}/quarterly-report.html?quarter=2020-Q1")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")

    e2e_page.wait_for_selector(".notable-empty", timeout=5000)
    text = e2e_page.locator(".notable-empty").first.text_content()
    assert text and "no economy data" in text.lower()

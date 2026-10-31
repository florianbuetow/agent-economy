"""Browser acceptance tests for concurrent quarterly navigation (T-175)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route


pytestmark = pytest.mark.integration

_REPORT_PATTERN = "**/api/quarterly-report?**"


def _open_seeded_first_quarter(page: Page, server_url: str) -> None:
    page.goto(f"{server_url}/quarterly-report.html?quarter=2026-Q1")
    page.wait_for_function(
        "() => document.getElementById('qr-quarter-label').textContent === '2026-Q1'"
    )


def _quarter_from_route(route: Route) -> str:
    values = parse_qs(urlparse(route.request.url).query).get("quarter")
    assert values is not None
    assert len(values) == 1
    return values[0]


def _fulfill_no_data(route: Route) -> None:
    quarter = _quarter_from_route(route)
    route.fulfill(
        status=404,
        json={
            "error": "no_data",
            "message": f"No economy data exists for quarter {quarter}",
            "details": {"quarter": quarter},
        },
    )


def _install_quarter_fetch_probe(page: Page) -> None:
    page.evaluate(
        """() => {
            const originalFetch = window.fetch.bind(window);
            window.__quarterFetchProbe = { requested: [], settled: 0 };
            window.fetch = async function(...args) {
                const url = new URL(String(args[0]), window.location.origin);
                if (url.pathname !== '/api/quarterly-report') {
                    return originalFetch(...args);
                }
                window.__quarterFetchProbe.requested.push(
                    url.searchParams.get('quarter')
                );
                try {
                    return await originalFetch(...args);
                } finally {
                    window.__quarterFetchProbe.settled += 1;
                }
            };
        }"""
    )


def _click_in_one_browser_task(page: Page, directions: list[str]) -> None:
    page.evaluate(
        """directions => {
            directions.forEach(direction => {
                document.getElementById(`qr-${direction}`).click();
            });
        }""",
        directions,
    )


def _wait_for_quarter_requests(page: Page, expected_count: int) -> list[str]:
    page.wait_for_function(
        "expected => window.__quarterFetchProbe.settled === expected",
        arg=expected_count,
    )
    requested = page.evaluate("() => window.__quarterFetchProbe.requested")
    assert isinstance(requested, list)
    assert all(isinstance(quarter, str) for quarter in requested)
    return requested


@pytest.mark.e2e
def test_repeated_prev_clicks_cross_year_boundary_without_losing_no_data_quarters(
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Each rapid Prev is applied in order, including across a year boundary."""
    _open_seeded_first_quarter(e2e_page, e2e_server)
    _install_quarter_fetch_probe(e2e_page)
    e2e_page.route(_REPORT_PATTERN, _fulfill_no_data)

    _click_in_one_browser_task(e2e_page, ["prev", "prev"])

    assert _wait_for_quarter_requests(e2e_page, 2) == ["2025-Q4", "2025-Q3"]
    expect(e2e_page.locator("#qr-quarter-label")).to_have_text("2025-Q3")
    expect(e2e_page.locator("#qr-content")).to_contain_text("No economy data exists for 2025-Q3")


@pytest.mark.e2e
def test_mixed_rapid_clicks_apply_every_direction_in_order(
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """Rapid mixed navigation derives every request from its predecessor."""
    _open_seeded_first_quarter(e2e_page, e2e_server)
    _install_quarter_fetch_probe(e2e_page)
    e2e_page.route(_REPORT_PATTERN, _fulfill_no_data)

    _click_in_one_browser_task(e2e_page, ["next", "next", "prev"])

    assert _wait_for_quarter_requests(e2e_page, 3) == [
        "2026-Q2",
        "2026-Q3",
        "2026-Q2",
    ]
    expect(e2e_page.locator("#qr-quarter-label")).to_have_text("2026-Q2")


@pytest.mark.e2e
def test_report_failure_remains_visible_and_later_navigation_recovers(
    e2e_page: Page,
    e2e_server: str,
) -> None:
    """A failed navigation is visible and does not poison a later request."""
    _open_seeded_first_quarter(e2e_page, e2e_server)
    _install_quarter_fetch_probe(e2e_page)
    response_number = 0

    def fulfill_failure_then_no_data(route: Route) -> None:
        nonlocal response_number
        response_number += 1
        if response_number == 1:
            route.fulfill(
                status=503,
                json={
                    "error": "service_unavailable",
                    "message": "Quarterly report is temporarily unavailable",
                    "details": {},
                },
            )
            return
        _fulfill_no_data(route)

    e2e_page.route(_REPORT_PATTERN, fulfill_failure_then_no_data)

    _click_in_one_browser_task(e2e_page, ["next"])
    assert _wait_for_quarter_requests(e2e_page, 1) == ["2026-Q2"]
    expect(e2e_page.locator("#qr-quarter-label")).to_have_text("2026-Q1")
    expect(e2e_page.locator("#qr-content")).to_contain_text("Could not load the quarterly report")

    _click_in_one_browser_task(e2e_page, ["next"])
    assert _wait_for_quarter_requests(e2e_page, 2) == ["2026-Q2", "2026-Q2"]
    expect(e2e_page.locator("#qr-quarter-label")).to_have_text("2026-Q2")
    expect(e2e_page.locator("#qr-content")).to_contain_text("No economy data exists for 2026-Q2")

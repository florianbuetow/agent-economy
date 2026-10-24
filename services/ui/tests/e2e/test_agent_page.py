"""E2E tests for the agent leaderboard / profile page (WP-08 item 8, T-095).

No Playwright specs existed for this page before WP-08 (only landing,
observatory, and task pages were covered — see tests/e2e/pages/). These are
the minimal "renders with live data" acceptance tests the WP-08 brief asks
for when no pre-existing spec is available.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from playwright.sync_api import Page


@pytest.mark.e2e
def test_agent_page_loads_leaderboard_by_default(e2e_page: Page) -> None:
    """With no ?agent_id, the page shows the sortable leaderboard."""
    e2e_page.goto(f"{e2e_page.base_url}/agent.html")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")

    assert e2e_page.title() != ""
    e2e_page.wait_for_selector(".lb-row", timeout=5000)
    rows = e2e_page.locator(".lb-row")
    assert rows.count() > 0

    first_name = rows.first.locator(".lb-name").text_content()
    assert first_name and first_name.strip() != ""


@pytest.mark.e2e
def test_agent_page_leaderboard_sort_select_changes_rows(e2e_page: Page) -> None:
    """Changing the sort dropdown re-fetches and re-renders the leaderboard."""
    e2e_page.goto(f"{e2e_page.base_url}/agent.html")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    e2e_page.wait_for_selector(".lb-row", timeout=5000)

    e2e_page.select_option("#lb-sort", "tasks_posted")
    e2e_page.wait_for_timeout(500)

    rows = e2e_page.locator(".lb-row")
    assert rows.count() > 0
    first_label = rows.first.locator(".label-sm").text_content()
    assert first_label == "Posted"


@pytest.mark.e2e
def test_agent_page_leaderboard_row_navigates_to_profile(e2e_page: Page) -> None:
    """Clicking a leaderboard row navigates to that agent's profile view."""
    e2e_page.goto(f"{e2e_page.base_url}/agent.html")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    e2e_page.wait_for_selector(".lb-row", timeout=5000)

    e2e_page.locator(".lb-row").first.click()
    e2e_page.wait_for_url("**/agent.html?agent_id=*")
    assert "agent_id=" in e2e_page.url


@pytest.mark.e2e
def test_agent_profile_page_renders_known_agent(e2e_page: Page) -> None:
    """?agent_id=a-alice renders that agent's real profile data."""
    e2e_page.goto(f"{e2e_page.base_url}/agent.html?agent_id=a-alice")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")

    e2e_page.wait_for_selector(".profile-name", timeout=5000)
    name = e2e_page.locator(".profile-name").text_content()
    assert name and name.strip() == "Alice"

    balance = e2e_page.locator(".profile-balance .amount").text_content()
    assert balance and balance.strip() != ""

    # Stats grid, earnings sparkline, and satisfaction bars all rendered.
    assert e2e_page.locator(".stat-grid .stat-cell").count() == 4
    assert e2e_page.locator("svg.sparkline").count() >= 1
    assert e2e_page.locator(".quality-row").count() == 2


@pytest.mark.e2e
def test_agent_profile_page_unknown_agent_shows_not_found(e2e_page: Page) -> None:
    """An unknown agent_id shows a not-found message instead of crashing."""
    e2e_page.goto(f"{e2e_page.base_url}/agent.html?agent_id=a-does-not-exist")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")

    e2e_page.wait_for_selector(".lb-empty", timeout=5000)
    text = e2e_page.locator(".lb-empty").text_content()
    assert text and "not found" in text.lower()


@pytest.mark.e2e
def test_agent_profile_back_link_returns_to_leaderboard(e2e_page: Page) -> None:
    """The back link on a profile view returns to the plain leaderboard URL."""
    e2e_page.goto(f"{e2e_page.base_url}/agent.html?agent_id=a-alice")  # type: ignore[attr-defined]
    e2e_page.wait_for_load_state("networkidle")
    e2e_page.wait_for_selector("#back-to-leaderboard", timeout=5000)

    e2e_page.locator("#back-to-leaderboard").click()
    e2e_page.wait_for_url("**/agent.html")
    assert "agent_id" not in e2e_page.url

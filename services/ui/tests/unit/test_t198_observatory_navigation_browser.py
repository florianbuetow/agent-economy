"""T-197 failing-first browser coverage for Observatory drilldown navigation."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, Page, Route

pytestmark = pytest.mark.unit
WEB = Path(__file__).resolve().parents[2] / "data/web"

METRICS = {
    "gdp": {
        "total": 150,
        "last_24h": 25,
        "last_7d": 100,
        "rate_per_hour": 2.5,
        "per_agent": 75,
        "delta_1h": 1.0,
        "delta_24h": 2.0,
    },
    "agents": {"total_registered": 2, "active": 2, "with_completed_tasks": 1, "delta_active": 0.0},
    "tasks": {
        "completed_24h": 1,
        "completed_all_time": 3,
        "open": 1,
        "in_execution": 0,
        "disputed": 0,
        "completion_rate": 0.75,
        "delta_open": 0.0,
        "delta_completed_24h": 0.0,
    },
    "escrow": {"total_locked": 25, "delta_locked": 0.0},
    "spec_quality": {
        "avg_score": 0.8,
        "extremely_satisfied_pct": 0.5,
        "satisfied_pct": 0.5,
        "dissatisfied_pct": 0.0,
        "trend_direction": "stable",
        "trend_delta": 0.0,
    },
    "labor_market": {
        "avg_bids_per_task": 1.5,
        "avg_reward": 50,
        "unemployment_rate": 0.0,
        "acceptance_latency_minutes": 4,
        "task_posting_rate": 0.5,
        "delta_avg_bids": 0.0,
        "delta_avg_reward": 0.0,
        "reward_distribution": {"0_to_10": 0, "11_to_50": 1, "51_to_100": 0, "over_100": 0},
    },
    "economy_phase": {"phase": "stable", "task_creation_trend": "stable"},
}

QUALITY = {"extremely_satisfied": 1, "satisfied": 0, "dissatisfied": 0}
STATS = {
    "total_earned": 100,
    "total_spent": 0,
    "tasks_completed_as_worker": 26,
    "tasks_posted": 26,
    "delivery_quality": QUALITY,
    "spec_quality": QUALITY,
}
AGENT_ID = "agent/leader?one"
POSTER_ID = "agent/poster?one"
POSTER_STATS = {
    **STATS,
    "total_earned": 0,
    "total_spent": 200,
    "tasks_completed_as_worker": 0,
}


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _common(route: Route) -> bool:
    url = route.request.url
    if url.endswith("/assets/shared.js"):
        route.fulfill(
            status=200,
            content_type="application/javascript",
            body=(WEB / "assets/shared.js").read_text(),
        )
    elif url.endswith("/assets/style.css"):
        route.fulfill(
            status=200,
            content_type="text/css",
            body=(WEB / "assets/style.css").read_text(),
        )
    elif url.endswith("/api/metrics"):
        route.fulfill(status=200, json=METRICS)
    elif url.endswith("/api/agents?sort_by=total_earned&order=desc&limit=50"):
        route.fulfill(
            status=200,
            json={
                "agents": [
                    {"agent_id": AGENT_ID, "name": "Leader <script>", "stats": STATS},
                    {"agent_id": POSTER_ID, "name": "Poster", "stats": POSTER_STATS},
                ]
            },
        )
    else:
        return False
    return True


def _open_observatory(browser: Browser) -> tuple[Page, list[str]]:
    page = browser.new_page()
    page.add_init_script("""
      window.__injected = false;
      window.EventSource = class { addEventListener() {} close() {} };
    """)
    requested: list[str] = []
    events = [
        {
            "event_id": 3,
            "event_source": "board",
            "event_type": "asset.uploaded",
            "timestamp": "2026-10-25T10:00:00Z",
            "task_id": "task/asset?one",
            "agent_id": "agent-worker",
            "summary": "Asset <img src=x onerror=window.__injected=true> uploaded",
            "payload": {"filename": "answer.txt", "size_bytes": 42},
        },
        {
            "event_id": 2,
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": "2026-10-25T09:00:00Z",
            "task_id": None,
            "agent_id": "agent/profile?one",
            "summary": "Agent registered",
            "payload": {},
        },
        {
            "event_id": 1,
            "event_source": "board",
            "event_type": "task.created",
            "timestamp": "2026-10-25T08:00:00Z",
            "task_id": "task/profile?one",
            "agent_id": "agent-worker",
            "summary": "Task created",
            "payload": {},
        },
    ]

    def respond(route: Route) -> None:
        requested.append(route.request.url)
        url = route.request.url
        if _common(route):
            return
        if url.endswith("/observatory.html"):
            route.fulfill(
                status=200, content_type="text/html", body=(WEB / "observatory.html").read_text()
            )
        elif url.endswith("/assets/observatory.js"):
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=(WEB / "assets/observatory.js").read_text(),
            )
        elif url.endswith("/api/metrics/gdp/history?window=24h&resolution=1h"):
            route.fulfill(status=200, json={"data_points": [{"gdp": 100}, {"gdp": 150}]})
        elif url.endswith("/api/events?limit=50"):
            route.fulfill(status=200, json={"events": events, "has_more": False})
        else:
            route.abort()

    page.route("**/*", respond)
    page.goto("http://observatory.test/observatory.html")
    expect(page.locator("#feed-scroll .feed-item")).to_have_count(3)
    return page, requested


def _task(role: str, index: int) -> dict[str, object]:
    return {
        "task_id": f"{role}/task?{index}",
        "title": f"{role.title()} job {index}",
        "status": "approved",
        "reward": index,
        "poster_id": AGENT_ID,
        "created_at": "2026-10-25T08:00:00Z",
        "bid_count": 1,
    }


def _open_agent_profile(
    browser: Browser,
    *,
    fail_initial_role: str | None = None,
    fail_next_role: str | None = None,
) -> tuple[Page, list[str]]:
    page = browser.new_page()
    requested: list[str] = []
    role_requests = {"posted": 0, "worked": 0}

    def respond(route: Route) -> None:
        requested.append(route.request.url)
        parsed = urlparse(route.request.url)
        if _common(route):
            return
        if parsed.path == "/agent.html":
            route.fulfill(
                status=200, content_type="text/html", body=(WEB / "agent.html").read_text()
            )
        elif parsed.path == "/assets/agent.js":
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=(WEB / "assets/agent.js").read_text(),
            )
        elif unquote(parsed.path) == f"/api/agents/{AGENT_ID}":
            route.fulfill(
                status=200,
                json={
                    "agent_id": AGENT_ID,
                    "name": "Leader",
                    "registered_at": "2026-01-01T00:00:00Z",
                    "balance": 10,
                    "stats": STATS,
                    "recent_tasks": [],
                    "recent_feedback": [],
                },
            )
        elif unquote(parsed.path) == f"/api/agents/{AGENT_ID}/earnings":
            route.fulfill(
                status=200,
                json={
                    "total_earned": 100,
                    "last_7d_earned": 0,
                    "avg_per_task": 50,
                    "tasks_approved": 2,
                    "data_points": [],
                },
            )
        elif parsed.path == "/api/tasks":
            query = parse_qs(parsed.query)
            role = "posted" if query.get("poster_id") == [AGENT_ID] else "worked"
            if role == "worked":
                assert query["worker_id"] == [AGENT_ID]
            offset = int(query["offset"][0])
            limit = int(query["limit"][0])
            role_requests[role] += 1
            if fail_initial_role == role and role_requests[role] == 1:
                route.fulfill(status=503, json={"error": "unavailable"})
                return
            if fail_next_role == role and offset > 0 and role_requests[role] == 2:
                route.fulfill(status=503, json={"error": "unavailable"})
                return
            tasks = [_task(role, index) for index in range(offset, min(offset + limit, 26))]
            route.fulfill(
                status=200,
                json={"tasks": tasks, "total_count": 26, "limit": limit, "offset": offset},
            )
        else:
            route.abort()

    page.route("**/*", respond)
    page.goto("http://agent.test/agent.html?agent_id=agent%2Fleader%3Fone")
    expect(page.locator(".profile-name")).to_have_text("Leader")
    return page, requested


def test_observatory_exposes_native_encoded_drilldown_links(browser: Browser) -> None:
    page, _ = _open_observatory(browser)
    try:
        expect(page.locator(".topnav a.topnav-link")).to_have_count(6)
        expect(page.locator(".topnav a[aria-current=page]")).to_have_text("Observatory")
        hrefs = page.locator("#feed-scroll a.feed-link").evaluate_all(
            "links => links.map(link => link.getAttribute('href'))"
        )
        assert hrefs == [
            "/task.html?task_id=task%2Fasset%3Fone#documents",
            "/agent.html?agent_id=agent%2Fprofile%3Fone",
            "/task.html?task_id=task%2Fprofile%3Fone",
        ]
        assert page.locator("#feed-scroll img").count() == 0
        assert page.evaluate("window.__injected") is False

        row = page.locator("#lb-scroll a.lb-row").first
        expect(row).to_have_attribute("href", "/agent.html?agent_id=agent%2Fleader%3Fone")
        row.focus()
        assert page.evaluate("document.activeElement.tagName") == "A"
        page.locator("#tab-posters").click()
        expect(page.locator("#lb-scroll a.lb-row").first).to_have_attribute(
            "href", "/agent.html?agent_id=agent%2Fposter%3Fone"
        )

        ticker = page.locator("#bottom-ticker-track")
        expect(ticker.locator('a.bt-item:has-text("TASKS/ALL")')).to_have_count(2)
        expect(ticker.locator('a.bt-item:has-text("TASKS/ALL")').first).to_have_attribute(
            "href", "/task.html?view=list"
        )
        expect(ticker.locator('a.bt-item:has-text("POST/RATE")').first).to_have_attribute(
            "href", "/task.html?view=list"
        )
        expect(ticker.locator('a.bt-item:has-text("GDP/TOTAL")').first).to_have_attribute(
            "href", "/quarterly-report.html"
        )
        expect(ticker.locator('a.bt-item:has-text("ESCROW/LOCK")')).to_have_count(0)
        expect(ticker.locator('span.bt-item:has-text("ESCROW/LOCK")')).to_have_count(2)
        expect(ticker.locator('a.bt-item:has-text("TOP/EARNER")').first).to_have_attribute(
            "href", "/agent.html?agent_id=agent%2Fleader%3Fone"
        )
        expect(ticker.locator('a.bt-item:has-text("AGENTS/REG")').first).to_have_attribute(
            "href", "/agent.html"
        )
        assert (
            ticker.evaluate("element => getComputedStyle(element).animationPlayState") == "running"
        )
        page.locator(".bottom-ticker").hover()
        assert (
            ticker.evaluate("element => getComputedStyle(element).animationPlayState") == "paused"
        )
        page.mouse.move(0, 0)
        ticker.locator("a.bt-item").first.focus()
        assert (
            ticker.evaluate("element => getComputedStyle(element).animationPlayState") == "paused"
        )
    finally:
        page.close()


def test_profile_makes_all_posted_and_worked_jobs_reachable(browser: Browser) -> None:
    page, requested = _open_agent_profile(browser)
    try:
        posted = page.locator("#posted-tasks a.list-row")
        worked = page.locator("#worked-tasks a.list-row")
        expect(posted).to_have_count(25)
        expect(worked).to_have_count(25)
        page.locator("#load-more-posted").click()
        page.locator("#load-more-worked").click()
        expect(posted).to_have_count(26)
        expect(worked).to_have_count(26)
        expect(posted.last).to_have_attribute("href", "/task.html?task_id=posted%2Ftask%3F25")
        expect(worked.last).to_have_attribute("href", "/task.html?task_id=worked%2Ftask%3F25")
        task_requests = [url for url in requested if urlparse(url).path == "/api/tasks"]
        assert len(task_requests) == 4
        assert sum("poster_id=agent%2Fleader%3Fone" in url for url in task_requests) == 2
        assert sum("worker_id=agent%2Fleader%3Fone" in url for url in task_requests) == 2
    finally:
        page.close()


def test_profile_distinguishes_initial_history_failure_from_no_jobs(browser: Browser) -> None:
    page, _ = _open_agent_profile(browser, fail_initial_role="posted")
    try:
        error = page.locator("#posted-history .task-load-error")
        expect(error).to_contain_text("unavailable")
        expect(page.locator("#retry-posted")).to_be_visible()
        expect(page.locator("#posted-history")).not_to_contain_text("No posted jobs yet")
        expect(page.locator("#worked-tasks a.list-row")).to_have_count(25)

        page.locator("#retry-posted").click()
        expect(page.locator("#posted-tasks a.list-row")).to_have_count(25)
        expect(error).to_have_count(0)
    finally:
        page.close()


def test_profile_retains_loaded_jobs_when_next_page_fails(browser: Browser) -> None:
    page, _ = _open_agent_profile(browser, fail_next_role="posted")
    try:
        posted = page.locator("#posted-tasks a.list-row")
        expect(posted).to_have_count(25)
        page.locator("#load-more-posted").click()

        expect(posted).to_have_count(25)
        expect(page.locator("#posted-history .task-load-error")).to_contain_text(
            "Could not load more"
        )
        expect(page.locator("#retry-posted")).to_be_visible()

        page.locator("#retry-posted").click()
        expect(posted).to_have_count(26)
    finally:
        page.close()

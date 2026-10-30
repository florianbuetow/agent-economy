"""Browser coverage for the task pages: navigation, list filters, links, errors, create form."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from playwright.sync_api import Browser, Page, Route

pytestmark = pytest.mark.unit

WEB = Path(__file__).resolve().parents[2] / "data/web"
OPERATOR = "a-operator"
BASE = "http://task.test"

_DEADLINES = {
    "bidding_deadline": "2026-10-25T09:00:00Z",
    "execution_deadline": "2026-10-25T10:00:00Z",
    "review_deadline": "2026-10-25T11:00:00Z",
}
_TIMESTAMPS = {"created_at": "2026-10-25T08:00:00Z"}


def _task(task_id: str, status: str, **overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "task_id": task_id,
        "title": f"Title {task_id}",
        "spec": "Do the thing.",
        "reward": 40,
        "status": status,
        "lifecycle_version": 3,
        "poster": {"agent_id": OPERATOR, "name": "Operator"},
        "worker": None,
        "bid_count": 0,
        "bids": [],
        "assets": [],
        "feedback": [],
        "dispute": None,
        "deadlines": _DEADLINES,
        "timestamps": _TIMESTAMPS,
    }
    task.update(overrides)
    return task


def _list_item(task_id: str, status: str, bid_count: int = 0) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "title": f"Title {task_id}",
        "status": status,
        "reward": 40,
        "poster_id": OPERATOR,
        "created_at": "2026-10-25T08:00:00Z",
        "bid_count": bid_count,
    }


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _serve_static(route: Route, decoded_path: str) -> bool:
    relative = decoded_path.removeprefix("/")
    if decoded_path in {"/task.html", "/quarterly-report.html", "/index.html"}:
        route.fulfill(status=200, content_type="text/html", body=(WEB / relative).read_text())
    elif decoded_path == "/assets/style.css":
        route.fulfill(status=200, content_type="text/css", body="")
    elif decoded_path.startswith("/assets/"):
        route.fulfill(
            status=200, content_type="application/javascript", body=(WEB / relative).read_text()
        )
    else:
        return False
    return True


def _open(
    browser: Browser,
    path: str,
    api: Callable[[str, dict[str, list[str]]], Any | None],
    *,
    identity: str | None = OPERATOR,
) -> tuple[Page, list[str]]:
    page = browser.new_page()
    requested: list[str] = []
    page.add_init_script("""
      window.EventSource = class {
        addEventListener() {}
        close() {}
      };
    """)

    def respond(route: Route) -> None:
        parsed = urlparse(route.request.url)
        requested.append(parsed.path + ("?" + parsed.query if parsed.query else ""))
        decoded_path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if _serve_static(route, decoded_path):
            return
        if decoded_path == "/api/proxy/identity":
            if identity is None:
                route.fulfill(status=503, json={"error": "unavailable"})
            else:
                route.fulfill(status=200, json={"agent_id": identity})
        elif decoded_path == "/api/proxy/browser-config":
            route.fulfill(
                status=200,
                json={"csrf_header_name": "X-ATE-CSRF", "csrf_header_value": "local-operator"},
            )
        elif decoded_path == "/api/metrics":
            route.fulfill(status=200, json={})
        elif decoded_path == "/api/agents":
            route.fulfill(status=200, json={"agents": []})
        elif decoded_path == "/api/events":
            route.fulfill(status=200, json={"events": [], "has_more": False})
        else:
            payload = api(decoded_path, query)
            if payload is None:
                route.abort()
            else:
                route.fulfill(status=200, json=payload)

    page.route("**/*", respond)
    page.goto(BASE + path)
    return page, requested


@pytest.mark.parametrize(
    "page_name",
    ["task.html", "quarterly-report.html", "index.html", "observatory.html", "agent.html"],
)
def test_static_navigation_has_jobs_and_post_anchors(page_name: str) -> None:
    html = (WEB / page_name).read_text()

    assert re.search(r'<a [^>]*href="/task\.html\?view=list"[^>]*>Jobs</a>', html)
    assert re.search(r'<a [^>]*href="/task\.html"[^>]*>Post</a>', html)
    assert ">Task Lifecycle<" not in html


def test_task_page_marks_the_active_nav_entry(browser: Browser) -> None:
    page, _ = _open(browser, "/task.html?view=list", lambda _p, _q: {"tasks": [], "total_count": 0})
    try:
        expect(page.locator("a[data-job-nav=jobs]")).to_have_class(re.compile("active"))
        expect(page.locator("a[data-job-nav=post]")).not_to_have_class(re.compile("active"))
    finally:
        page.close()


def test_job_list_filters_are_sent_to_the_tasks_api(browser: Browser) -> None:
    def api(path: str, _query: dict[str, list[str]]) -> Any | None:
        if path == "/api/tasks":
            return {"tasks": [_list_item("t-one", "open")], "total_count": 1}
        return None

    page, requested = _open(browser, "/task.html?view=list", api)
    try:
        expect(page.locator("#task-list .task-list-row")).to_have_count(1)
        page.locator("#task-filter-status").select_option("submitted")
        expect(page.locator("#task-filter-status")).to_have_value("submitted")
        page.locator("#task-filter-scope").select_option("poster")
        expect(page.locator("#task-filter-scope")).to_have_value("poster")
        page.locator("#task-filter-scope").select_option("worker")
        expect(page.locator("#task-filter-scope")).to_have_value("worker")
        page.wait_for_timeout(100)
    finally:
        page.close()

    list_queries = [
        parse_qs(urlparse(url).query)
        for url in requested
        if url.startswith("/api/tasks?") and "limit=25" in url
    ]
    assert list_queries[-2]["status"] == ["submitted"]
    assert list_queries[-2]["poster_id"] == [OPERATOR]
    assert list_queries[-1]["worker_id"] == [OPERATOR]
    assert "poster_id" not in list_queries[-1]


def test_needs_my_action_panel_lists_bids_to_accept_and_deliveries_to_review(
    browser: Browser,
) -> None:
    def api(path: str, query: dict[str, list[str]]) -> Any | None:
        if path != "/api/tasks":
            return None
        if query.get("poster_id") == [OPERATOR] and query.get("status") == ["open"]:
            return {
                "tasks": [_list_item("t-bids", "open", 2), _list_item("t-quiet", "open", 0)],
                "total_count": 2,
            }
        if query.get("poster_id") == [OPERATOR] and query.get("status") == ["submitted"]:
            return {"tasks": [_list_item("t-review", "submitted", 1)], "total_count": 1}
        return {"tasks": [], "total_count": 0}

    page, _ = _open(browser, "/task.html?view=list", api)
    try:
        panel = page.locator("#needs-action")
        expect(panel).to_contain_text("Bids waiting for you to accept")
        expect(panel).to_contain_text("2 bids")
        expect(panel).to_contain_text("Deliveries waiting for your review")
        expect(panel.locator("a.task-list-row")).to_have_count(2)
        expect(panel.locator("a[href='/task.html?task_id=t-bids']")).to_have_count(1)
        expect(panel.locator("a[href='/task.html?task_id=t-review']")).to_have_count(1)
        expect(panel.locator("a[href='/task.html?task_id=t-quiet']")).to_have_count(0)
    finally:
        page.close()


def test_needs_my_action_panel_says_so_when_nothing_is_waiting(browser: Browser) -> None:
    page, _ = _open(browser, "/task.html?view=list", lambda _p, _q: {"tasks": [], "total_count": 0})
    try:
        expect(page.locator("#needs-action")).to_contain_text("Nothing needs your action")
    finally:
        page.close()


def test_every_agent_name_on_the_task_page_is_an_anchor(browser: Browser) -> None:
    task = _task(
        "t-links",
        "ruled",
        worker={"agent_id": "a-worker", "name": "Worker <b>Bee</b>"},
        poster={"agent_id": "a-poster/1", "name": "Poster"},
        dispute={
            "claim_id": "c-1",
            "reason": "Bad.",
            "rebuttal": {"content": "No."},
            "ruling": {"worker_pct": 50, "summary": "Split."},
        },
        feedback=[
            {
                "feedback_id": "f-1",
                "from_agent_id": "a-worker",
                "from_agent_name": "Worker <b>Bee</b>",
                "to_agent_id": "a-poster/1",
                "to_agent_name": "Poster",
                "category": "spec_quality",
                "rating": "satisfied",
                "comment": "ok",
                "visible": True,
            }
        ],
        financial_effect={
            "schema_version": 1,
            "settlement_state": "complete",
            "receipt_state": "confirmed",
            "outcome": "split",
            "operation_id": "op-12345678-1234-4234-8234-123456789012",
            "receipt_reference": "op-12345678-1234-4234-8234-123456789012",
            "business_projection_pending": False,
            "pending_target_status": None,
        },
    )
    page, _ = _open(
        browser,
        "/task.html?task_id=t-links",
        lambda path, _q: task if path == "/api/tasks/t-links" else None,
    )
    try:
        expect(page.locator("#phase-content")).to_contain_text("Settlement")
        worker_hrefs = page.locator("a[href='/agent.html?agent_id=a-worker']")
        poster_hrefs = page.locator("a[href='/agent.html?agent_id=a-poster%2F1']")
        assert poster_hrefs.count() >= 1
        assert worker_hrefs.count() >= 2
        assert page.locator("#phase-content b").count() == 0

        # Walk the earlier phases too: Review shows dispute and rebuttal authors.
        page.locator(".phase-step[data-phase='4']").click()
        expect(page.locator(".dispute-panel a.agent-link")).to_have_attribute(
            "href", "/agent.html?agent_id=a-poster%2F1"
        )
        expect(page.locator(".rebuttal-panel a.agent-link")).to_have_attribute(
            "href", "/agent.html?agent_id=a-worker"
        )
        page.locator(".phase-step[data-phase='2']").click()
        expect(page.locator("#task-section-2 .card-body a.agent-link")).to_have_count(2)
    finally:
        page.close()


def test_bidders_are_anchors_on_the_bid_phase(browser: Browser) -> None:
    task = _task(
        "t-bids",
        "open",
        bid_count=1,
        bids=[
            {
                "bid_id": "b-1",
                "bidder": {
                    "agent_id": "a-bidder",
                    "name": "Bidder",
                    "delivery_quality": {
                        "extremely_satisfied": 1,
                        "satisfied": 0,
                        "dissatisfied": 0,
                    },
                },
                "amount": 30,
                "submitted_at": "2026-10-25T08:30:00Z",
                "accepted": False,
            }
        ],
    )

    def api(path: str, _query: dict[str, list[str]]) -> Any | None:
        if path == "/api/tasks/t-bids":
            return task
        if path == "/api/proxy/tasks/t-bids/bids":
            return {"task_id": "t-bids", "bids": task["bids"]}
        return None

    page, _ = _open(browser, "/task.html?task_id=t-bids", api)
    try:
        expect(page.locator(".bid-name a.agent-link")).to_have_attribute(
            "href", "/agent.html?agent_id=a-bidder"
        )
    finally:
        page.close()


@pytest.mark.parametrize(
    ("status", "expected", "absent"),
    [
        ("cancelled", "THIS TASK WAS CANCELLED", "WAITING FOR BIDS"),
        ("expired", "THIS TASK EXPIRED", "WAITING FOR BIDS"),
        ("open", "WAITING FOR BIDS", "CANCELLED"),
    ],
)
def test_terminal_states_show_their_real_state(
    browser: Browser, status: str, expected: str, absent: str
) -> None:
    task = _task("t-state", status)
    page, _ = _open(
        browser,
        "/task.html?task_id=t-state",
        lambda path, _q: task if path == "/api/tasks/t-state" else None,
    )
    try:
        expect(page.locator("#phase-content")).to_contain_text(expected)
        expect(page.locator("#phase-content")).not_to_contain_text(absent)
    finally:
        page.close()


def test_missing_operator_identity_is_explained_on_the_page(browser: Browser) -> None:
    task = _task("t-noid", "open")
    page, _ = _open(
        browser,
        "/task.html?task_id=t-noid",
        lambda path, _q: task if path == "/api/tasks/t-noid" else None,
        identity=None,
    )
    try:
        notice = page.locator("#action-error")
        expect(notice).to_be_visible()
        expect(notice).to_contain_text("operator account could not be identified")
        expect(notice).to_contain_text("Next step")
    finally:
        page.close()


def test_accept_failure_before_sending_is_visible_on_the_page(browser: Browser) -> None:
    broken = _task(
        "t-accept",
        "open",
        bid_count=1,
        lifecycle_version=None,
        bids=[
            {
                "bid_id": "b-1",
                "bidder": {
                    "agent_id": "a-bidder",
                    "name": "Bidder",
                    "delivery_quality": {
                        "extremely_satisfied": 0,
                        "satisfied": 0,
                        "dissatisfied": 0,
                    },
                },
                "amount": 30,
                "submitted_at": "2026-10-25T08:30:00Z",
                "accepted": False,
            }
        ],
    )

    def api(path: str, _query: dict[str, list[str]]) -> Any | None:
        if path == "/api/tasks/t-accept":
            return broken
        if path == "/api/proxy/tasks/t-accept/bids":
            return {"task_id": "t-accept", "bids": broken["bids"]}
        return None

    page, _ = _open(browser, "/task.html?task_id=t-accept", api)
    try:
        page.locator(".btn-accept-bid").click()
        notice = page.locator("#action-error")
        expect(notice).to_be_visible()
        expect(notice).to_contain_text("has not finished loading")
        expect(notice).to_contain_text("Nothing was sent")
        expect(page.locator(".btn-accept-bid")).to_be_enabled()
    finally:
        page.close()


def test_create_form_labels_balance_escrow_and_duration_hints(browser: Browser) -> None:
    def api(path: str, _query: dict[str, list[str]]) -> Any | None:
        if path == f"/api/agents/{OPERATOR}":
            return {"agent_id": OPERATOR, "name": "Operator", "balance": 120}
        return None

    page, _ = _open(browser, "/task.html", api)
    try:
        expect(page.locator("#f-balance")).to_contain_text("Your balance: 120")
        expect(page.locator(".form-note")).to_contain_text("locked in escrow")
        for field in ("f-title", "f-spec", "f-reward", "f-bid-dl", "f-accept-window"):
            assert page.locator(f"label[for={field}]").count() == 1
        for field in ("f-exec-dl", "f-rev-dl"):
            assert page.locator(f"label[for={field}]").count() == 1
        for field in ("f-bid-dl", "f-accept-window", "f-exec-dl", "f-rev-dl"):
            assert page.locator(f"#{field}").get_attribute("placeholder") is None
            hint = page.locator(f"#{field}-hint")
            expect(hint).to_contain_text("whole seconds")
            expect(page.locator(f"#{field}")).to_have_attribute("aria-describedby", f"{field}-hint")
        expect(page.locator("a[data-job-nav=post]")).to_have_class(re.compile("active"))
    finally:
        page.close()


def test_reward_above_balance_blocks_submit_with_a_clear_message(browser: Browser) -> None:
    def api(path: str, _query: dict[str, list[str]]) -> Any | None:
        if path == f"/api/agents/{OPERATOR}":
            return {"agent_id": OPERATOR, "name": "Operator", "balance": 120}
        return None

    page, requested = _open(browser, "/task.html", api)
    try:
        page.locator("#f-title").fill("Too expensive")
        page.locator("#f-spec").fill("Costs more than I have.")
        page.locator("#f-reward").fill("121")
        for field in ("f-bid-dl", "f-accept-window", "f-exec-dl", "f-rev-dl"):
            page.locator(f"#{field}").fill("60")
        page.locator("#btn-post-task").click()

        expect(page.locator("#post-error")).to_contain_text("more than your balance (120")
        expect(page.locator("#post-error")).to_contain_text("lower the reward")
    finally:
        page.close()

    assert not any("task-postings/prepare" in url for url in requested)

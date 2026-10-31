"""T-198 browser coverage for permanent, safe task document access."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, Page, Route

pytestmark = pytest.mark.unit
WEB = Path(__file__).resolve().parents[2] / "data/web"
TASK_ID = "task/completed?one"
HTML_ASSET_ID = "asset/html?one"
FAILED_ASSET_ID = "asset/missing?two"


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture
def task_page(browser: Browser) -> Generator[tuple[Page, list[str]], None, None]:
    page = browser.new_page()
    requested: list[str] = []
    page.add_init_script("""
      window.__assetExecuted = false;
      window.__eventSourceListeners = {};
      window.EventSource = class {
        addEventListener(type, listener) { window.__eventSourceListeners[type] = listener; }
        close() {}
      };
    """)
    task = {
        "task_id": TASK_ID,
        "title": "Completed document exchange",
        "spec": "Build the login page and document its API.",
        "reward": 100,
        "status": "approved",
        "lifecycle_version": 7,
        "poster": {"agent_id": "poster-one", "name": "Poster"},
        "worker": {"agent_id": "worker-one", "name": "Worker"},
        "bid_count": 1,
        "bids": [],
        "assets": [
            {
                "asset_id": HTML_ASSET_ID,
                "uploader_id": "worker-one",
                "filename": "login-page.html",
                "content_type": "application/octet-stream",
                "size_bytes": 63,
                "content_hash": "a" * 64,
                "uploaded_at": "2026-10-25T10:00:00Z",
            },
            {
                "asset_id": FAILED_ASSET_ID,
                "uploader_id": "worker-one",
                "filename": "api-spec.md",
                "content_type": "application/octet-stream",
                "size_bytes": 18,
                "content_hash": "b" * 64,
                "uploaded_at": "2026-10-25T10:01:00Z",
            },
        ],
        "feedback": [],
        "dispute": {
            "reason": "The first delivery omitted validation.",
            "rebuttal": {"content": "Validation is documented in the API specification."},
            "ruling": {"worker_pct": 100, "summary": "The worker met the written specification."},
        },
        "deadlines": {
            "bidding_deadline": "2026-10-25T09:00:00Z",
            "execution_deadline": "2026-10-25T10:00:00Z",
            "review_deadline": "2026-10-25T11:00:00Z",
        },
        "timestamps": {
            "created_at": "2026-10-25T08:00:00Z",
            "accepted_at": "2026-10-25T09:00:00Z",
            "submitted_at": "2026-10-25T10:00:00Z",
            "approved_at": "2026-10-25T11:00:00Z",
        },
    }

    def respond(route: Route) -> None:
        requested.append(route.request.url)
        parsed = urlparse(route.request.url)
        decoded_path = unquote(parsed.path)
        if parsed.path == "/task.html":
            route.fulfill(
                status=200, content_type="text/html", body=(WEB / "task.html").read_text()
            )
        elif parsed.path == "/assets/style.css":
            route.fulfill(status=200, content_type="text/css", body="")
        elif parsed.path.startswith("/assets/"):
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=(WEB / parsed.path.removeprefix("/")).read_text(),
            )
        elif decoded_path == f"/api/tasks/{TASK_ID}":
            route.fulfill(status=200, json=task)
        elif decoded_path == f"/api/proxy/tasks/{TASK_ID}/assets/{HTML_ASSET_ID}":
            route.fulfill(
                status=200,
                content_type="application/octet-stream",
                body="<script>window.__assetExecuted = true</script><h1>Login</h1>",
            )
        elif decoded_path == f"/api/proxy/tasks/{TASK_ID}/assets/{FAILED_ASSET_ID}":
            route.fulfill(status=410, content_type="text/plain", body="Gone")
        elif parsed.path == "/api/proxy/identity":
            route.fulfill(status=200, json={"agent_id": "poster-one"})
        elif parsed.path == "/api/metrics":
            route.fulfill(status=200, json={})
        elif parsed.path == "/api/agents":
            route.fulfill(status=200, json={"agents": []})
        elif parsed.path == "/api/events":
            route.fulfill(status=200, json={"events": [], "has_more": False})
        else:
            route.abort()

    page.route("**/*", respond)
    page.goto("http://task.test/task.html?task_id=task%2Fcompleted%3Fone#documents")
    expect(page.locator("#documents")).to_be_visible()
    try:
        yield page, requested
    finally:
        page.close()


@pytest.fixture
def browse_page(browser: Browser) -> Generator[Page, None, None]:
    page = browser.new_page()
    page.add_init_script("window.EventSource = class { addEventListener() {} close() {} };")
    task_summary = {
        "task_id": "task/select?one",
        "title": "Selectable completed task",
        "status": "approved",
        "reward": 55,
        "poster_id": "poster-one",
        "created_at": "2026-10-25T08:00:00Z",
        "bid_count": 1,
    }
    selected_task = {
        **task_summary,
        "spec": "Open this lifecycle from the task browser.",
        "lifecycle_version": 7,
        "poster": {"agent_id": "poster-one", "name": "Poster"},
        "worker": {"agent_id": "worker-one", "name": "Worker"},
        "bids": [],
        "assets": [],
        "feedback": [],
        "dispute": None,
        "deadlines": {
            "bidding_deadline": "2026-10-25T09:00:00Z",
            "execution_deadline": "2026-10-25T10:00:00Z",
            "review_deadline": "2026-10-25T11:00:00Z",
        },
        "timestamps": {
            "created_at": "2026-10-25T08:00:00Z",
            "accepted_at": "2026-10-25T09:00:00Z",
            "submitted_at": "2026-10-25T10:00:00Z",
            "approved_at": "2026-10-25T11:00:00Z",
        },
    }

    def respond(route: Route) -> None:
        parsed = urlparse(route.request.url)
        decoded_path = unquote(parsed.path)
        if parsed.path == "/task.html":
            route.fulfill(
                status=200, content_type="text/html", body=(WEB / "task.html").read_text()
            )
        elif parsed.path == "/assets/style.css":
            route.fulfill(status=200, content_type="text/css", body="")
        elif parsed.path.startswith("/assets/"):
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=(WEB / parsed.path.removeprefix("/")).read_text(),
            )
        elif parsed.path == "/api/tasks" and parsed.query == "limit=25&offset=0":
            route.fulfill(
                status=200,
                json={"tasks": [task_summary], "total_count": 1, "limit": 25, "offset": 0},
            )
        elif decoded_path == "/api/tasks/task/select?one":
            route.fulfill(status=200, json=selected_task)
        elif parsed.path == "/api/proxy/identity":
            route.fulfill(status=200, json={"agent_id": "observer-one"})
        elif parsed.path == "/api/metrics":
            route.fulfill(status=200, json={})
        elif parsed.path == "/api/agents":
            route.fulfill(status=200, json={"agents": []})
        elif parsed.path == "/api/events":
            route.fulfill(status=200, json={"events": [], "has_more": False})
        else:
            route.abort()

    page.route("**/*", respond)
    page.goto("http://task.test/task.html?view=list")
    try:
        yield page
    finally:
        page.close()


def test_completed_task_exposes_every_exchanged_document(task_page: tuple[Page, list[str]]) -> None:
    page, _ = task_page
    documents = page.locator("#documents")
    expect(documents).to_contain_text("Build the login page and document its API.")
    expect(documents).to_contain_text("The first delivery omitted validation.")
    expect(documents).to_contain_text("Validation is documented in the API specification.")
    expect(documents).to_contain_text("The worker met the written specification.")

    filename = documents.get_by_role("link", name="login-page.html")
    expect(filename).to_have_attribute(
        "href", "/api/proxy/tasks/task%2Fcompleted%3Fone/assets/asset%2Fhtml%3Fone"
    )
    expect(filename).to_have_attribute("download", "login-page.html")
    first_file = documents.locator(".document-file").first
    expect(first_file.get_by_role("link", name="Worker")).to_have_attribute(
        "href", "/agent.html?agent_id=worker-one"
    )
    expect(first_file).to_contain_text("a" * 64)

    downloads = documents.locator(".document-actions a[download]")
    expect(downloads).to_have_count(2)
    expect(downloads.first).to_have_attribute(
        "href", "/api/proxy/tasks/task%2Fcompleted%3Fone/assets/asset%2Fhtml%3Fone"
    )
    expect(downloads.first).to_have_attribute("download", "login-page.html")


def test_text_preview_is_inert_and_uses_encoded_native_asset_path(
    task_page: tuple[Page, list[str]],
) -> None:
    page, requested = task_page
    page.get_by_role("button", name="View login-page.html as text").click()
    preview = page.locator('[data-asset-preview="asset/html?one"]')
    expect(preview).to_contain_text("<script>window.__assetExecuted = true</script>")
    assert page.evaluate("window.__assetExecuted") is False
    assert page.locator("#documents iframe").count() == 0
    assert page.locator("#documents script").count() == 0
    assert any(
        unquote(urlparse(url).path) == f"/api/proxy/tasks/{TASK_ID}/assets/{HTML_ASSET_ID}"
        for url in requested
    )

    with page.expect_response(
        lambda response: unquote(urlparse(response.url).path) == f"/api/tasks/{TASK_ID}"
    ):
        page.evaluate(
            """taskId => window.__eventSourceListeners.economy_event({data: JSON.stringify({
              task_id: taskId, event_type: 'feedback.submitted', event_source: 'reputation',
              timestamp: '2026-10-25T12:00:00Z', summary: 'Unchanged task refreshed'
            })})""",
            TASK_ID,
        )
    expect(preview).to_contain_text("<script>window.__assetExecuted = true</script>")


def test_preview_failure_is_visible_without_removing_download(
    task_page: tuple[Page, list[str]],
) -> None:
    page, _ = task_page
    page.get_by_role("button", name="View api-spec.md as text").click()
    preview = page.locator('[data-asset-preview="asset/missing?two"]')
    expect(preview).to_contain_text("410")
    expect(page.locator('#documents .document-actions a[download="api-spec.md"]')).to_be_visible()


def test_documents_remain_available_while_navigating_all_completed_phases(
    task_page: tuple[Page, list[str]],
) -> None:
    page, _ = task_page
    for phase in range(7):
        page.locator(f'.phase-step[data-phase="{phase}"]').click()
        expect(page.locator("#documents")).to_be_visible()
        filename = page.locator("#documents").get_by_role("link", name="login-page.html")
        expect(filename).to_be_visible()


def test_task_browser_opens_a_selected_task_lifecycle(browse_page: Page) -> None:
    link = browse_page.locator('#task-list a[href="/task.html?task_id=task%2Fselect%3Fone"]')
    expect(link.locator(".task-list-title")).to_have_text("Selectable completed task")
    link.click()
    expect(browse_page).to_have_url("http://task.test/task.html?task_id=task%2Fselect%3Fone")
    expect(browse_page.locator("#documents")).to_contain_text(
        "Open this lifecycle from the task browser."
    )

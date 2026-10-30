"""Browser flow for posting with input files: prepare -> admit -> upload -> publish (lane U)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from mp05_posting_fixtures import POSTING_TERMS, agent_config, scope
from playwright.sync_api import expect, sync_playwright
from service_auth.financial_identity import operation_id_for

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, Page, Route

pytestmark = pytest.mark.unit

TASK_POSTING_JS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "web" / "assets" / "task-posting.js"
)
PAGE_URL = "http://localhost/task.html"
PUBLICATION_ID = "op-70000000-0000-4000-8000-000000000001"
TASK_ID = "t-70000000-0000-4000-8000-000000000001"
CONFIG = agent_config()
AGENT_ID = "a-ui-native"

PAGE_HTML = """<!doctype html>
<html>
  <body>
    <input id="f-title">
    <textarea id="f-spec"></textarea>
    <input id="f-reward">
    <input id="f-bid-dl">
    <input id="f-accept-window">
    <input id="f-exec-dl">
    <input id="f-rev-dl">
    <input id="f-inputs" type="file" multiple>
    <button id="btn-post-task">Post Task</button>
    <span id="post-error"></span>
    <script>window.ATE = {};</script>
    <script src="/assets/task-posting.js"></script>
    <script>
      window.ATE.TaskPosting.ready().then(async function() {
        window.ATE.TaskPosting.bindCreateForm();
        await window.ATE.TaskPosting.recoverExisting();
        window.__postingReady = true;
      });
    </script>
  </body>
</html>
"""


def _record(expected_input_count: int) -> dict[str, Any]:
    intent: dict[str, Any] = {
        "protocol_version": 2,
        "publication_operation_id": PUBLICATION_ID,
        "task_id": TASK_ID,
        "actor_kind": "agent",
        "actor_id": AGENT_ID,
        "action": "task_publication_admit",
        "resource": "POST /task-publications",
        "lock_operation_id": operation_id_for("task-lock", (TASK_ID, AGENT_ID)),
        **POSTING_TERMS,
        "expected_input_count": expected_input_count,
    }
    rendered = json.dumps(intent, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    record_scope = scope(CONFIG)
    record_scope["operator_agent_id"] = AGENT_ID
    return {
        "record_version": 2,
        "scope": record_scope,
        "intent": intent,
        "request_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
    }


def _envelope(record: dict[str, Any], state: str) -> dict[str, Any]:
    return {
        "protocol_version": 2,
        "publication_operation_id": PUBLICATION_ID,
        "task_id": TASK_ID,
        "request_sha256": record["request_sha256"],
        "publication_state": state,
    }


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(headless=True)
        yield launched
        launched.close()


@pytest.fixture
def page(browser: Browser) -> Generator[Page, None, None]:
    context = browser.new_context()
    test_page = context.new_page()
    try:
        yield test_page
    finally:
        context.close()


def test_attached_files_post_through_admit_upload_publish(page: Page, tmp_path: Path) -> None:
    record = _record(2)
    calls: list[tuple[str, dict[str, str]]] = []

    def route_request(route: Route) -> None:
        url = route.request.url
        headers = route.request.headers
        if url.endswith("/api/proxy/browser-config"):
            route.fulfill(
                status=200,
                json={"csrf_header_name": "X-ATE-CSRF", "csrf_header_value": "local-operator"},
            )
        elif url.endswith("/assets/task-posting.js"):
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=TASK_POSTING_JS_PATH.read_text(),
            )
        elif url == PAGE_URL:
            route.fulfill(status=200, content_type="text/html", body=PAGE_HTML)
        elif url.endswith("/api/proxy/task-publications/prepare"):
            calls.append(("prepare", headers))
            assert route.request.post_data_json["expected_input_count"] == 2
            route.fulfill(status=200, json=record)
        elif url.endswith(f"/{PUBLICATION_ID}/admit"):
            calls.append(("admit", headers))
            route.fulfill(status=202, json=_envelope(record, "awaiting_inputs"))
        elif url.endswith(f"/{PUBLICATION_ID}/inputs"):
            calls.append(("inputs", headers))
            route.fulfill(status=201, json={"artifact_id": "x", "role": "input"})
        elif url.endswith(f"/{PUBLICATION_ID}/publish"):
            calls.append(("publish", headers))
            route.fulfill(status=201, json=_envelope(record, "published"))
        else:
            route.abort()

    page.route("http://localhost/**", route_request)
    page.goto(PAGE_URL)
    page.wait_for_function("() => window.__postingReady === true")
    first = tmp_path / "input.txt"
    first.write_text("3 7 11\n")
    second = tmp_path / "data.csv"
    second.write_text("a,b\n1,2\n")
    for selector, key in (
        ("#f-title", "title"),
        ("#f-spec", "spec"),
        ("#f-reward", "reward"),
        ("#f-bid-dl", "bidding_deadline_seconds"),
        ("#f-accept-window", "acceptance_window_seconds"),
        ("#f-exec-dl", "execution_deadline_seconds"),
        ("#f-rev-dl", "review_deadline_seconds"),
    ):
        page.locator(selector).fill(str(POSTING_TERMS[key]))
    page.locator("#f-inputs").set_input_files([str(first), str(second)])
    page.locator("#btn-post-task").click()

    link = page.locator(f'[data-task-posting-link="{TASK_ID}"]')
    expect(link).to_be_visible()
    assert [name for name, _ in calls] == ["prepare", "admit", "inputs", "inputs", "publish"]
    upload_keys = [headers["idempotency-key"] for name, headers in calls if name == "inputs"]
    assert upload_keys == [f"{PUBLICATION_ID}:input:0", f"{PUBLICATION_ID}:input:1"]
    assert all(headers["x-ate-csrf"] == "local-operator" for _, headers in calls)
    stored = page.evaluate(f"() => localStorage.getItem('ate.task-posting:v2:{PUBLICATION_ID}')")
    assert json.loads(stored) == record

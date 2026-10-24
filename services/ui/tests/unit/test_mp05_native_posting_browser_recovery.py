"""Browser regressions for durable MP-05 posting recovery and acknowledgment parsing."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from mp05_posting_fixtures import POSTING_TERMS, agent_config, prepared_record, publication
from playwright.sync_api import expect, sync_playwright
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, Page, Route

pytestmark = pytest.mark.unit

TASK_POSTING_JS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "web" / "assets" / "task-posting.js"
)
PAGE_URL = "http://localhost/task.html"
FIRST_OPERATION_ID = "op-90000000-0000-4000-8000-000000000001"
SECOND_OPERATION_ID = "op-10000000-0000-4000-8000-000000000002"
FIRST_TASK_ID = "t-90000000-0000-4000-8000-000000000001"
SECOND_TASK_ID = "t-10000000-0000-4000-8000-000000000002"

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


@pytest.fixture(scope="module")
def native_browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(native_browser: Browser) -> Generator[Page, None, None]:
    context = native_browser.new_context()
    test_page = context.new_page()
    try:
        yield test_page
    finally:
        context.close()


_CONFIG = agent_config()


def _intent(
    operation_id: str,
    task_id: str,
    *,
    title: str = str(POSTING_TERMS["title"]),
    spec: str = str(POSTING_TERMS["spec"]),
    reward: int = int(POSTING_TERMS["reward"]),
) -> TaskPostingIntent:
    return TaskPostingIntent.from_json(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": task_id,
            "actor_kind": "agent",
            "actor_id": "a-ui-native",
            "lock_operation_id": operation_id_for("task-lock", (task_id, "a-ui-native")),
            **POSTING_TERMS,
            "title": title,
            "spec": spec,
            "reward": reward,
        }
    )


FIRST_INTENT = _intent(FIRST_OPERATION_ID, FIRST_TASK_ID)
SECOND_INTENT = _intent(
    SECOND_OPERATION_ID,
    SECOND_TASK_ID,
    title="A separate native UI posting",
    reward=76,
)
FIRST_RECORD = prepared_record(_CONFIG, FIRST_INTENT)
SECOND_RECORD = prepared_record(_CONFIG, SECOND_INTENT)


def _observation(
    intent: TaskPostingIntent,
    state: str,
    *,
    observation: str,
    publication_value: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status_code = (
        200 if observation == "status" else {"published": 201, "awaiting_lock": 202}[state]
    )
    return {
        "observation": observation,
        "status_code": status_code,
        "publication": (
            publication(intent, state) if publication_value is None else publication_value
        ),
    }


def _base_route(route: Route) -> bool:
    url = route.request.url
    if url.endswith("/api/proxy/browser-config"):
        route.fulfill(
            status=200,
            json={"csrf_header_name": "X-ATE-CSRF", "csrf_header_value": "local-operator"},
        )
        return True
    if url.endswith("/assets/task-posting.js"):
        route.fulfill(
            status=200,
            content_type="application/javascript",
            body=TASK_POSTING_JS_PATH.read_text(),
        )
        return True
    if url == PAGE_URL:
        route.fulfill(status=200, content_type="text/html", body=PAGE_HTML)
        return True
    return False


def _open(page: Page) -> None:
    page.goto(PAGE_URL)
    page.wait_for_function("() => window.__postingReady === true")


def _fill_form(page: Page, terms: dict[str, object]) -> None:
    page.locator("#f-title").fill(str(terms["title"]))
    page.locator("#f-spec").fill(str(terms["spec"]))
    page.locator("#f-reward").fill(str(terms["reward"]))
    page.locator("#f-bid-dl").fill(str(terms["bidding_deadline_seconds"]))
    page.locator("#f-accept-window").fill(str(terms["acceptance_window_seconds"]))
    page.locator("#f-exec-dl").fill(str(terms["execution_deadline_seconds"]))
    page.locator("#f-rev-dl").fill(str(terms["review_deadline_seconds"]))


def test_completed_posting_exposes_explicit_separate_posting_action(page: Page) -> None:
    prepared = [FIRST_RECORD, SECOND_RECORD]
    prepare_bodies: list[dict[str, Any]] = []
    submitted_operations: list[str] = []

    def route_request(route: Route) -> None:
        url = route.request.url
        if url.endswith("/api/proxy/task-postings/prepare"):
            prepare_bodies.append(route.request.post_data_json)
            route.fulfill(status=200, json=prepared[len(prepare_bodies) - 1])
            return
        for intent in (FIRST_INTENT, SECOND_INTENT):
            if url.endswith(f"/api/proxy/task-postings/{intent.operation_id}/submit"):
                submitted_operations.append(intent.operation_id)
                route.fulfill(
                    status=201,
                    json=_observation(intent, "published", observation="submit"),
                )
                return
        if not _base_route(route):
            route.abort()

    page.route("http://localhost/**", route_request)
    _open(page)
    _fill_form(page, POSTING_TERMS)
    page.locator("#btn-post-task").click()

    completed = page.locator(f'[data-task-posting-operation="{FIRST_OPERATION_ID}"]')
    expect(completed).to_have_count(1)
    expect(completed).to_contain_text("Posting completed")
    separate = completed.get_by_role("button", name="Create separate posting")
    expect(separate).to_be_visible()

    second_terms = {**POSTING_TERMS, "title": SECOND_INTENT.title, "reward": SECOND_INTENT.reward}
    _fill_form(page, second_terms)
    separate.click()

    expect(page.locator(f'[data-task-posting-operation="{SECOND_OPERATION_ID}"]')).to_contain_text(
        "Posting completed"
    )
    assert [body["title"] for body in prepare_bodies] == [
        FIRST_INTENT.title,
        SECOND_INTENT.title,
    ]
    assert submitted_operations == [FIRST_OPERATION_ID, SECOND_OPERATION_ID]


def test_multiple_recovered_records_require_operation_specific_retry(page: Page) -> None:
    status_operations: list[str] = []
    submit_operations: list[str] = []

    def route_request(route: Route) -> None:
        url = route.request.url
        for intent in (FIRST_INTENT, SECOND_INTENT):
            if url.endswith(f"/api/proxy/task-postings/{intent.operation_id}/status"):
                status_operations.append(intent.operation_id)
                route.fulfill(
                    status=200,
                    json=_observation(intent, "awaiting_lock", observation="status"),
                )
                return
            if url.endswith(f"/api/proxy/task-postings/{intent.operation_id}/submit"):
                submit_operations.append(intent.operation_id)
                route.fulfill(
                    status=202,
                    json=_observation(intent, "awaiting_lock", observation="submit"),
                )
                return
        if not _base_route(route):
            route.abort()

    page.route("http://localhost/**", route_request)
    _open(page)
    page.evaluate(
        """records => {
          for (const record of records) {
            localStorage.setItem(
              'ate.task-posting:v1:' + record.intent.operation_id,
              JSON.stringify(record)
            );
          }
        }""",
        [FIRST_RECORD, SECOND_RECORD],
    )
    page.reload()
    page.wait_for_function("() => window.__postingReady === true")

    expect(page.locator("[data-task-posting-operation]")).to_have_count(2)
    expect(page.locator(f'[data-task-posting-operation="{FIRST_OPERATION_ID}"]')).to_contain_text(
        FIRST_INTENT.title
    )
    expect(page.locator(f'[data-task-posting-operation="{SECOND_OPERATION_ID}"]')).to_contain_text(
        SECOND_INTENT.title
    )
    assert status_operations == [FIRST_OPERATION_ID, SECOND_OPERATION_ID]

    page.locator("#btn-post-task").click()
    page.wait_for_timeout(100)
    assert submit_operations == []
    expect(page.locator("[data-task-posting-general]")).to_contain_text(
        "Choose the saved posting you want to retry"
    )

    first = page.locator(f'[data-task-posting-operation="{FIRST_OPERATION_ID}"]')
    with page.expect_request(
        lambda request: request.url.endswith(
            f"/api/proxy/task-postings/{FIRST_OPERATION_ID}/submit"
        )
    ):
        first.get_by_role("button", name="Retry exact intent").click()
    assert submit_operations == [FIRST_OPERATION_ID]


def test_single_recovered_record_remains_the_generic_exact_retry(page: Page) -> None:
    submit_operations: list[str] = []

    def route_request(route: Route) -> None:
        url = route.request.url
        if url.endswith(f"/api/proxy/task-postings/{FIRST_OPERATION_ID}/status"):
            route.fulfill(
                status=200,
                json=_observation(FIRST_INTENT, "awaiting_lock", observation="status"),
            )
            return
        if url.endswith(f"/api/proxy/task-postings/{FIRST_OPERATION_ID}/submit"):
            submit_operations.append(FIRST_OPERATION_ID)
            route.fulfill(
                status=202,
                json=_observation(FIRST_INTENT, "awaiting_lock", observation="submit"),
            )
            return
        if not _base_route(route):
            route.abort()

    page.route("http://localhost/**", route_request)
    _open(page)
    page.evaluate(
        """record => localStorage.setItem(
          'ate.task-posting:v1:' + record.intent.operation_id,
          JSON.stringify(record)
        )""",
        FIRST_RECORD,
    )
    page.reload()
    page.wait_for_function("() => window.__postingReady === true")

    with page.expect_request(
        lambda request: request.url.endswith(
            f"/api/proxy/task-postings/{FIRST_OPERATION_ID}/submit"
        )
    ):
        page.locator("#btn-post-task").click()
    assert submit_operations == [FIRST_OPERATION_ID]


def test_generic_post_is_guarded_while_saved_status_recovery_is_pending(page: Page) -> None:
    held_status_routes: list[Route] = []
    prepare_requests: list[str] = []
    submit_requests: list[str] = []

    def route_request(route: Route) -> None:
        url = route.request.url
        if url.endswith(f"/api/proxy/task-postings/{FIRST_OPERATION_ID}/status"):
            held_status_routes.append(route)
            return
        if url.endswith("/api/proxy/task-postings/prepare"):
            prepare_requests.append(route.request.post_data or "")
            route.fulfill(status=200, json=SECOND_RECORD)
            return
        if url.endswith(f"/api/proxy/task-postings/{SECOND_OPERATION_ID}/submit"):
            submit_requests.append(route.request.post_data or "")
            route.fulfill(
                status=202,
                json=_observation(SECOND_INTENT, "awaiting_lock", observation="submit"),
            )
            return
        if not _base_route(route):
            route.abort()

    page.route("http://localhost/**", route_request)
    _open(page)
    page.evaluate(
        """record => localStorage.setItem(
          'ate.task-posting:v1:' + record.intent.operation_id,
          JSON.stringify(record)
        )""",
        FIRST_RECORD,
    )
    page.reload()
    page.wait_for_timeout(100)
    assert len(held_status_routes) == 1

    second_terms = {**POSTING_TERMS, "title": SECOND_INTENT.title, "reward": SECOND_INTENT.reward}
    _fill_form(page, second_terms)
    page.locator("#btn-post-task").click()
    page.wait_for_timeout(100)

    assert prepare_requests == []
    assert submit_requests == []
    expect(page.locator("[data-task-posting-general]")).to_contain_text(
        "Checking saved posting status"
    )

    held_status_routes[0].fulfill(
        status=200,
        json=_observation(FIRST_INTENT, "awaiting_lock", observation="status"),
    )
    page.wait_for_function("() => window.__postingReady === true")


@pytest.mark.parametrize(
    ("title", "spec"),
    [
        ("😀" * 200, str(POSTING_TERMS["spec"])),
        (str(POSTING_TERMS["title"]), "😀" * 10_000),
    ],
)
def test_prepared_text_limits_count_unicode_code_points(
    page: Page,
    title: str,
    spec: str,
) -> None:
    posting_intent = _intent(FIRST_OPERATION_ID, FIRST_TASK_ID, title=title, spec=spec)
    record = prepared_record(_CONFIG, posting_intent)
    terms = {**POSTING_TERMS, "title": title, "spec": spec}
    submitted: list[str] = []

    def route_request(route: Route) -> None:
        url = route.request.url
        if url.endswith("/api/proxy/task-postings/prepare"):
            route.fulfill(status=200, json=record)
            return
        if url.endswith(f"/api/proxy/task-postings/{FIRST_OPERATION_ID}/submit"):
            submitted.append(FIRST_OPERATION_ID)
            route.fulfill(
                status=202,
                json=_observation(posting_intent, "awaiting_lock", observation="submit"),
            )
            return
        if not _base_route(route):
            route.abort()

    page.route("http://localhost/**", route_request)
    _open(page)
    _fill_form(page, terms)
    page.locator("#btn-post-task").click()
    page.wait_for_timeout(100)

    assert submitted == [FIRST_OPERATION_ID]


@pytest.mark.parametrize("case", ["over_limit_astral", "unpaired_surrogate"])
def test_invalid_prepared_unicode_text_never_reaches_submit(page: Page, case: str) -> None:
    record = copy.deepcopy(FIRST_RECORD)
    invalid_title = "😀" * 201 if case == "over_limit_astral" else "\ud800"
    record["intent"]["title"] = invalid_title
    submitted: list[str] = []

    def route_request(route: Route) -> None:
        url = route.request.url
        if url.endswith("/api/proxy/task-postings/prepare"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(record),
            )
            return
        if url.endswith(f"/api/proxy/task-postings/{FIRST_OPERATION_ID}/submit"):
            submitted.append(FIRST_OPERATION_ID)
            route.abort()
            return
        if not _base_route(route):
            route.abort()

    page.route("http://localhost/**", route_request)
    _open(page)
    _fill_form(page, POSTING_TERMS)
    if case == "over_limit_astral":
        page.locator("#f-title").fill(invalid_title)
    else:
        page.evaluate("() => { document.getElementById('f-title').value = '\\ud800'; }")
    page.locator("#btn-post-task").click()
    page.wait_for_timeout(100)

    assert submitted == []


@pytest.mark.parametrize(
    "raw_record",
    [
        "{not-json",
        json.dumps({"record_version": 1}),
    ],
    ids=["malformed-json", "invalid-structure"],
)
def test_corrupt_saved_record_is_visible_and_blocks_implicit_fresh_posting(
    page: Page,
    raw_record: str,
) -> None:
    prepare_requests: list[str] = []
    corrupt_key = "ate.task-posting:v1:op-70000000-0000-4000-8000-000000000007"

    def route_request(route: Route) -> None:
        if route.request.url.endswith("/api/proxy/task-postings/prepare"):
            prepare_requests.append(route.request.post_data or "")
            route.abort()
            return
        if not _base_route(route):
            route.abort()

    page.route("http://localhost/**", route_request)
    _open(page)
    page.evaluate("([key, raw]) => localStorage.setItem(key, raw)", [corrupt_key, raw_record])
    page.reload()
    page.wait_for_function("() => window.__postingReady === true")

    diagnostic = page.locator(f'[data-task-posting-corrupt="{corrupt_key}"]')
    expect(diagnostic).to_contain_text("invalid")
    _fill_form(page, POSTING_TERMS)
    page.locator("#btn-post-task").click()
    page.wait_for_timeout(100)

    assert prepare_requests == []
    expect(page.locator("[data-task-posting-general]")).to_contain_text(
        "Resolve the invalid saved posting record"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid_admitted_at",
        "publication_precedes_admission",
        "non_open_result",
        "wrong_result_escrow",
        "wrong_created_at",
        "wrong_bidding_cutoff",
        "wrong_lifecycle_version",
        "wrong_timing_contract_version",
        "invalid_event_id",
    ],
)
def test_malformed_published_semantics_never_create_completion_marker(
    page: Page,
    mutation: str,
) -> None:
    malformed = copy.deepcopy(publication(FIRST_INTENT, "published"))
    result = malformed["result"]
    assert isinstance(result, dict)
    if mutation == "invalid_admitted_at":
        malformed["admitted_at"] = "2026-10-19T09:00:00Z"
    elif mutation == "publication_precedes_admission":
        malformed["admitted_at"] = "2026-10-19T09:00:02.000000Z"
    elif mutation == "non_open_result":
        result["status"] = "approved"
    elif mutation == "wrong_result_escrow":
        result["escrow_id"] = "esc-other"
    elif mutation == "wrong_created_at":
        result["created_at"] = "2026-10-19T09:00:02.000000Z"
    elif mutation == "wrong_bidding_cutoff":
        result["bidding_cutoff_us"] = int(result["bidding_cutoff_us"]) + 1
    elif mutation == "wrong_lifecycle_version":
        result["lifecycle_version"] = 1
    elif mutation == "wrong_timing_contract_version":
        result["timing_contract_version"] = 1
    else:
        result["event_id"] = 0

    def route_request(route: Route) -> None:
        url = route.request.url
        if url.endswith("/api/proxy/task-postings/prepare"):
            route.fulfill(status=200, json=FIRST_RECORD)
            return
        if url.endswith(f"/api/proxy/task-postings/{FIRST_OPERATION_ID}/submit"):
            route.fulfill(
                status=201,
                json=_observation(
                    FIRST_INTENT,
                    "published",
                    observation="submit",
                    publication_value=malformed,
                ),
            )
            return
        if not _base_route(route):
            route.abort()

    page.route("http://localhost/**", route_request)
    _open(page)
    _fill_form(page, POSTING_TERMS)
    page.locator("#btn-post-task").click()

    expect(page.locator("#post-error")).to_contain_text(
        "Posting acknowledgment does not match the saved intent"
    )
    marker = page.evaluate(
        "operationId => localStorage.getItem('ate.task-posting-completion:v1:' + operationId)",
        FIRST_OPERATION_ID,
    )
    assert marker is None

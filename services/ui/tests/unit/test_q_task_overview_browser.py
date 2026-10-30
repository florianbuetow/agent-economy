"""Browser coverage for the single task overview and operator participation controls.

The page talks to stubbed same-origin APIs; a controllable EventSource lets the
test push a live task event and observe the re-render.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from playwright.sync_api import expect, sync_playwright

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, Page, Route

pytestmark = pytest.mark.unit

WEB = Path(__file__).resolve().parents[2] / "data/web"
OPERATOR = "a-operator"
BASE = "http://task.test"
FAR_FUTURE = "2099-01-01T00:00:00Z"
_LOCKED = {
    "schema_version": 1,
    "settlement_state": "locked",
    "receipt_state": "absent",
    "outcome": None,
    "operation_id": None,
    "receipt_reference": None,
    "business_projection_pending": False,
    "pending_target_status": None,
}
_PAID = {
    **_LOCKED,
    "settlement_state": "complete",
    "receipt_state": "confirmed",
    "outcome": "worker_paid",
    "operation_id": "op-12345678-1234-4234-8234-123456789012",
    "receipt_reference": "op-12345678-1234-4234-8234-123456789012",
}


def _task(status: str, **overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "task_id": "t-job",
        "title": "Sum the numbers",
        "spec": "Return the sum of 2 and 3.",
        "reward": 40,
        "status": status,
        "lifecycle_version": 3,
        "poster": {"agent_id": "a-poster", "name": "Poster"},
        "worker": None,
        "bid_count": 0,
        "accepted_price": None,
        "bids": [],
        "assets": [],
        "feedback": [],
        "dispute": None,
        "deadlines": {
            "bidding_deadline": FAR_FUTURE,
            "acceptance_deadline": FAR_FUTURE,
            "execution_deadline": None,
            "review_deadline": None,
        },
        "timestamps": {"created_at": "2026-10-25T08:00:00Z"},
        "financial_effect": _LOCKED,
    }
    task.update(overrides)
    return task


def _operation(command: str, source: str, target: str, effect: str) -> dict[str, Any]:
    return {
        "task_id": "t-job",
        "status": target,
        "lifecycle_version": 4,
        "operation": {
            "protocol_version": 1,
            "operation_id": "op-00000000-0000-4000-8000-000000000000",
            "task_id": "t-job",
            "command": command,
            "request_sha256": "a" * 64,
            "operation_state": "completed",
            "source_status": source,
            "source_lifecycle_version": 3,
            "target_status": target,
            "target_lifecycle_version": 4,
            "effect_kind": effect,
            "effect_state": "none" if effect == "none" else "succeeded",
            "effect_operation_id": None,
            "effect_result_reference": None,
            "effect_intent": None if effect == "none" else {},
            "event_id": 9,
            "result": {},
        },
    }


class _Harness:
    """Stubbed backend: one mutable task, recorded writes, scripted write replies."""

    def __init__(self, task: dict[str, Any]) -> None:
        self.task = task
        self.writes: list[dict[str, Any]] = []
        self.requested: list[str] = []

    def write_reply(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        action = path.rsplit("/", 1)[-1]
        if action == "cancel":
            reply = _operation("cancel_task", "open", "cancelled", "bank_release")
            return 200, _with_operation_id(reply, body)
        if action == "bids":
            return 201, {
                "bid_id": "bid-1",
                "task_id": "t-job",
                "bidder_id": OPERATOR,
                "amount": body["amount"],
                "submitted_at": "2026-10-30T10:00:00Z",
            }
        if action == "submit":
            reply = _operation("submit_deliverable", "accepted", "submitted", "none")
            return 200, _with_operation_id(reply, body)
        if action == "assets":
            return 201, {"asset_id": "asset-1", "filename": "answer.txt"}
        if action in {"rebuttal", "feedback"}:
            return 200 if action == "rebuttal" else 201, {"ok": True}
        return 404, {"error": "not_found", "message": "unknown", "details": {}}


def _with_operation_id(reply: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    reply["operation"]["operation_id"] = body["operation_id"]
    reply["operation"]["source_lifecycle_version"] = body["expected_lifecycle_version"]
    reply["operation"]["target_lifecycle_version"] = body["expected_lifecycle_version"] + 1
    reply["lifecycle_version"] = body["expected_lifecycle_version"] + 1
    return reply


@pytest.fixture(scope="module")
def browser() -> Generator[Browser, None, None]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        try:
            yield instance
        finally:
            instance.close()


def _open(browser: Browser, harness: _Harness) -> Page:
    page = browser.new_page()
    page.add_init_script("""
      window.__sse = [];
      window.EventSource = class {
        constructor(url) { this.url = url; this.listeners = {}; window.__sse.push(this); }
        addEventListener(name, fn) { this.listeners[name] = fn; }
        close() {}
      };
      window.__pushEvent = function(event) {
        window.__sse.forEach(function(source) {
          var fn = source.listeners.economy_event;
          if (fn) fn({ data: JSON.stringify(event) });
        });
      };
    """)
    page.on("dialog", lambda dialog: dialog.accept())

    def respond(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path = unquote(parsed.path)
        harness.requested.append(path + ("?" + parsed.query if parsed.query else ""))
        if path in {"/task.html"}:
            route.fulfill(
                status=200, content_type="text/html", body=(WEB / "task.html").read_text()
            )
        elif path == "/assets/style.css":
            route.fulfill(status=200, content_type="text/css", body="")
        elif path.startswith("/assets/"):
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body=(WEB / path.removeprefix("/")).read_text(),
            )
        elif path == "/api/proxy/identity":
            route.fulfill(status=200, json={"agent_id": OPERATOR})
        elif path == "/api/proxy/browser-config":
            route.fulfill(
                status=200,
                json={"csrf_header_name": "X-ATE-CSRF", "csrf_header_value": "local-operator"},
            )
        elif path == "/api/metrics":
            route.fulfill(status=200, json={})
        elif path == "/api/agents":
            route.fulfill(status=200, json={"agents": []})
        elif path == "/api/events":
            query = parse_qs(parsed.query)
            events = []
            if query.get("task_id") == ["t-job"]:
                events = [
                    {
                        "event_id": 3,
                        "task_id": "t-job",
                        "event_source": "board",
                        "event_type": "task.created",
                        "timestamp": "2026-10-25T08:00:00Z",
                        "summary": "Old task event",
                        "agent_id": "a-poster",
                        "payload": {},
                    }
                ]
            route.fulfill(
                status=200, json={"events": events, "has_more": False, "newest_event_id": 3}
            )
        elif path == "/api/tasks/t-job":
            route.fulfill(status=200, json=harness.task)
        elif request.method == "POST" and path.startswith("/api/proxy/tasks/t-job/"):
            content_type = request.headers.get("content-type", "")
            body: dict[str, Any] = (
                json.loads(request.post_data or "{}")
                if content_type.startswith("application/json")
                else {}
            )
            harness.writes.append(
                {
                    "path": path,
                    "body": body,
                    "content_type": content_type,
                    "idempotency_key": request.headers.get("idempotency-key"),
                    "csrf": request.headers.get("x-ate-csrf"),
                    "raw": request.post_data_buffer or b"",
                }
            )
            status, reply = harness.write_reply(path, body)
            route.fulfill(status=status, json=reply)
        else:
            route.abort()

    page.route("**/*", respond)
    page.goto(BASE + "/task.html?task_id=t-job")
    return page


def test_overview_shows_the_whole_job_at_once(browser: Browser) -> None:
    task = _task(
        "ruled",
        worker={"agent_id": "a-worker", "name": "Worker"},
        accepted_price=30,
        bid_count=1,
        bids=[
            {
                "bid_id": "b-1",
                "bidder": {"agent_id": "a-worker", "name": "Worker", "delivery_quality": None},
                "amount": 30,
                "submitted_at": "2026-10-25T08:30:00Z",
                "accepted": True,
            }
        ],
        dispute={
            "claim_id": "c-1",
            "reason": "Wrong sum.",
            "filed_at": "2026-10-25T09:00:00Z",
            "rebuttal": {"content": "The sum is right.", "submitted_at": "2026-10-25T09:10:00Z"},
            "ruling": {
                "ruling_id": "r-1",
                "worker_pct": 80,
                "summary": "Mostly right.",
                "ruled_at": "2026-10-25T10:00:00Z",
            },
        },
        deadlines={
            "bidding_deadline": "2026-10-25T08:45:00Z",
            "acceptance_deadline": "2026-10-25T08:50:00Z",
            "execution_deadline": "2026-10-25T09:30:00Z",
            "review_deadline": FAR_FUTURE,
        },
    )
    harness = _Harness(task)
    page = _open(browser, harness)
    try:
        content = page.locator("#phase-content")
        expect(content).to_contain_text("Return the sum of 2 and 3.")
        expect(content).to_contain_text("Accepted price")
        expect(content).to_contain_text("30 ©")
        for label in ("Bidding closes", "Acceptance closes", "Delivery due", "Review closes"):
            expect(content).to_contain_text(label)
        expect(page.locator(".countdown[data-deadline]")).to_have_count(5)
        expect(
            page.locator(".countdown[data-deadline='2099-01-01T00:00:00Z']").first
        ).to_contain_text("in ")
        expect(page.locator(".bid-row")).to_have_count(1)
        expect(page.locator(".dispute-panel")).to_contain_text("Wrong sum.")
        expect(page.locator(".rebuttal-panel")).to_contain_text("The sum is right.")
        expect(page.locator(".ruling-card").first).to_contain_text("Mostly right.")
        expect(page.locator(".payment-status")).to_have_text("Held in escrow")
    finally:
        page.close()
    assert "/api/events?limit=50&task_id=t-job" in harness.requested


def test_live_refresh_keeps_selected_phase_and_typed_dispute_reason(browser: Browser) -> None:
    task = _task(
        "submitted",
        poster={"agent_id": OPERATOR, "name": "Operator"},
        worker={"agent_id": "a-worker", "name": "Worker"},
        accepted_price=30,
    )
    harness = _Harness(task)
    page = _open(browser, harness)
    try:
        page.locator("#btn-dispute-show").click()
        page.locator("#f-dispute-reason").fill("Half typed reason")
        page.locator(".phase-step[data-phase='2']").click()
        expect(page.locator(".phase-step[data-phase='2']")).to_have_class(
            "phase-step completed active"
        )
        harness.task = {**task, "title": "Sum the numbers (updated)"}
        page.evaluate("window.__pushEvent({event_id: 10, task_id: 't-job', event_type: 'x'})")
        expect(page.locator("#phase-content")).to_contain_text("Sum the numbers (updated)")
        expect(page.locator("#panel-title")).to_have_text("Contract")
        expect(page.locator("#f-dispute-reason")).to_have_value("Half typed reason")
        expect(page.locator("#dispute-form")).to_be_visible()
        expect(page.locator(".phase-step[data-phase='2']")).to_have_class(
            "phase-step completed active"
        )
        expect(page.locator("#task-section-2")).to_have_class("task-section selected")
    finally:
        page.close()


def test_poster_cancels_an_open_task(browser: Browser) -> None:
    harness = _Harness(_task("open", poster={"agent_id": OPERATOR, "name": "Operator"}))
    page = _open(browser, harness)
    try:
        expect(page.locator("#f-bid-amount")).to_have_count(0)
        page.locator("#btn-cancel-task").click()
        expect(page.locator("#action-error")).to_have_text(
            "Task cancelled. The reward is refunded to your balance."
        )
        expect(page.locator("#action-error")).to_have_class("action-error action-success")
    finally:
        page.close()
    (write,) = harness.writes
    assert write["path"] == "/api/proxy/tasks/t-job/cancel"
    assert write["body"]["expected_lifecycle_version"] == 3
    assert write["csrf"] == "local-operator"


def test_poster_leaves_feedback_after_approval(browser: Browser) -> None:
    task = _task(
        "approved",
        poster={"agent_id": OPERATOR, "name": "Operator"},
        worker={"agent_id": "a-worker", "name": "Worker"},
        financial_effect=_PAID,
    )
    harness = _Harness(task)
    page = _open(browser, harness)
    try:
        expect(page.locator(".payment-status")).to_have_text("Paid to the worker")
        page.locator("#f-feedback-rating").select_option("extremely_satisfied")
        page.locator("#f-feedback-comment").fill("Fast and correct")
        page.locator("#btn-submit-feedback").click()
        expect(page.locator("#action-error")).to_have_text("Feedback sent.")
        expect(page.locator("#btn-submit-feedback")).to_have_count(0)
    finally:
        page.close()
    assert harness.writes[0]["path"] == "/api/proxy/tasks/t-job/feedback"
    assert harness.writes[0]["body"] == {
        "rating": "extremely_satisfied",
        "comment": "Fast and correct",
    }


def test_operator_bids_uploads_and_submits_as_worker(browser: Browser) -> None:
    harness = _Harness(_task("open", bid_count=1))
    page = _open(browser, harness)
    try:
        expect(page.locator("#btn-cancel-task")).to_have_count(0)
        page.locator("#f-bid-amount").fill("35")
        page.locator("#btn-submit-bid").click()
        expect(page.locator("#action-error")).to_contain_text("Your bid was placed")

        harness.task = _task(
            "accepted",
            worker={"agent_id": OPERATOR, "name": "Operator"},
            accepted_price=35,
            lifecycle_version=5,
            deadlines={**_task("open")["deadlines"], "execution_deadline": FAR_FUTURE},
        )
        page.evaluate("window.__pushEvent({event_id: 11, task_id: 't-job', event_type: 'x'})")
        expect(page.locator("#btn-upload-asset")).to_be_visible()
        expect(page.locator("#btn-submit-deliverable")).to_have_count(0)
        page.locator("#f-upload-file").set_input_files(
            {"name": "answer.txt", "mimeType": "text/plain", "buffer": b"5"}
        )
        expect(page.locator("#f-upload-selected")).to_have_text("Selected: answer.txt")
        harness.task = {
            **harness.task,
            "assets": [
                {
                    "asset_id": "asset-1",
                    "uploader_id": OPERATOR,
                    "filename": "answer.txt",
                    "content_type": "text/plain",
                    "size_bytes": 1,
                    "content_hash": "c" * 64,
                    "uploaded_at": "2026-10-30T10:00:00Z",
                }
            ],
        }
        page.locator("#btn-upload-asset").click()
        expect(page.locator("#action-error")).to_contain_text("Uploaded answer.txt")
        page.locator("#btn-submit-deliverable").click()
        expect(page.locator("#action-error")).to_have_text(
            "Work submitted. The poster now reviews it."
        )
    finally:
        page.close()

    bid, upload, submit = harness.writes
    assert bid["path"] == "/api/proxy/tasks/t-job/bids"
    assert bid["body"]["amount"] == 35
    assert upload["path"] == "/api/proxy/tasks/t-job/assets"
    assert upload["content_type"].startswith("multipart/form-data")
    assert upload["idempotency_key"].startswith("op-")
    assert b"answer.txt" in upload["raw"]
    assert submit["path"] == "/api/proxy/tasks/t-job/submit"
    assert submit["body"]["expected_lifecycle_version"] == 5


def test_disputed_worker_submits_a_rebuttal(browser: Browser) -> None:
    task = _task(
        "disputed",
        worker={"agent_id": OPERATOR, "name": "Operator"},
        dispute={
            "claim_id": "c-1",
            "reason": "Wrong sum.",
            "filed_at": "2026-10-25T09:00:00Z",
            "rebuttal": None,
            "ruling": None,
        },
    )
    harness = _Harness(task)
    page = _open(browser, harness)
    try:
        expect(page.locator(".payment-status")).to_have_text("Held in escrow until the court rules")
        page.locator("#f-rebuttal").fill("2 + 3 is 5.")
        page.locator("#btn-submit-rebuttal").click()
        expect(page.locator("#action-error")).to_contain_text("Rebuttal sent to the court")
    finally:
        page.close()
    assert harness.writes[0]["path"] == "/api/proxy/tasks/t-job/rebuttal"
    assert harness.writes[0]["body"] == {"rebuttal": "2 + 3 is 5."}


def test_controls_are_hidden_from_parties_they_do_not_belong_to(browser: Browser) -> None:
    accepted_elsewhere = _task("accepted", worker={"agent_id": "a-worker", "name": "Worker"})
    page = _open(browser, _Harness(accepted_elsewhere))
    try:
        expect(page.locator("#phase-content")).to_contain_text("Contract Signed")
        for selector in (
            "#btn-upload-asset",
            "#btn-submit-deliverable",
            "#btn-cancel-task",
            "#f-bid-amount",
            "#btn-submit-rebuttal",
            "#btn-submit-feedback",
        ):
            expect(page.locator(selector)).to_have_count(0)
    finally:
        page.close()

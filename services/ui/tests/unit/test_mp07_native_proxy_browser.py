"""Browser-level failing-first contract for MP-07 UI mutation intents."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect, sync_playwright
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent

if TYPE_CHECKING:
    from collections.abc import Generator

    from playwright.sync_api import Browser, Page, Route

pytestmark = pytest.mark.unit

TASK_JS_PATH = Path(__file__).resolve().parents[2] / "data" / "web" / "assets" / "task.js"
TASK_POSTING_JS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "web" / "assets" / "task-posting.js"
)
TASK_URL = "http://ui.test/task.html"
TASK_ID = "t-native-ui"
POSTING_TASK_ID = "t-00000000-0000-4000-8000-000000000703"
POSTING_OPERATION_ID = "op-00000000-0000-4000-8000-000000000704"
FALLBACK_OPERATION_ID = "op-00000000-0000-4000-8000-000000000799"
FOREIGN_OPERATION_ID = "op-00000000-0000-4000-8000-000000000798"

PAGE_HTML = """<!doctype html>
<html>
  <body>
    <div id="panel-title"></div>
    <div id="task-status"></div>
    <div id="phase-strip"></div>
    <main id="phase-content"></main>
    <div id="feed-scroll"></div>
    <div id="ticker-track"></div>
    <script src="/assets/task-posting.js"></script>
    <script src="/assets/task.js"></script>
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


TASK_VIEW: dict[str, Any] = {
    "task_id": TASK_ID,
    "poster": {"agent_id": "a-ui-native", "name": "Operator"},
    "worker": None,
    "title": "Native UI task",
    "spec": "Keep the caller intent explicit.",
    "reward": 75,
    "status": "open",
    "lifecycle_version": 7,
    "bid_count": 1,
    "deadlines": {
        "bidding_deadline": "2026-10-18T12:00:00Z",
        "execution_deadline": None,
        "review_deadline": None,
    },
    "timestamps": {
        "created_at": "2026-10-18T11:58:00Z",
        "accepted_at": None,
        "submitted_at": None,
        "approved_at": None,
    },
    "bids": [],
    "assets": [],
    "feedback": [],
    "dispute": None,
}

SUBMITTED_TASK_VIEW: dict[str, Any] = {
    **TASK_VIEW,
    "status": "submitted",
    "bids": [],
    "assets": [
        {
            "asset_id": "asset-native",
            "filename": "answer.txt",
            "content_type": "text/plain",
            "size_bytes": 12,
        }
    ],
}

APPROVED_TASK_VIEW: dict[str, Any] = {
    **SUBMITTED_TASK_VIEW,
    "status": "approved",
    "lifecycle_version": 8,
}

ACCEPTED_TASK_VIEW: dict[str, Any] = {
    **TASK_VIEW,
    "status": "accepted",
    "lifecycle_version": 8,
    "worker": {"agent_id": "a-worker", "name": "Worker"},
    "bids": [],
    "timestamps": {
        **TASK_VIEW["timestamps"],
        "accepted_at": "2026-10-18T12:01:00Z",
    },
    "deadlines": {
        **TASK_VIEW["deadlines"],
        "execution_deadline": "2026-10-18T12:06:00Z",
    },
}

DISPUTED_TASK_VIEW: dict[str, Any] = {
    **SUBMITTED_TASK_VIEW,
    "status": "disputed",
    "lifecycle_version": 8,
    "worker": {"agent_id": "a-worker", "name": "Worker"},
    "dispute": {
        "claim_id": "clm-native",
        "reason": "Terms are incomplete.",
        "filed_at": "2026-10-18T12:04:00Z",
        "rebuttal": None,
        "ruling": None,
    },
}

AUTHORIZED_BIDS = {
    "task_id": TASK_ID,
    "bids": [
        {
            "bid_id": "bid-native",
            "bidder": {
                "agent_id": "a-worker",
                "name": "Worker",
                "delivery_quality": {
                    "extremely_satisfied": 1,
                    "satisfied": 0,
                    "dissatisfied": 0,
                },
            },
            "amount": 60,
            "submitted_at": "2026-10-18T11:59:00Z",
            "accepted": False,
        }
    ],
}

POSTING_TERMS = {
    "title": "Native UI task",
    "spec": "Keep the caller intent explicit.",
    "reward": 75,
    "bidding_deadline_seconds": 120,
    "acceptance_window_seconds": 45,
    "execution_deadline_seconds": 300,
    "review_deadline_seconds": 90,
}
_POSTING_INTENT = TaskPostingIntent.from_json(
    {
        "protocol_version": 1,
        "operation_id": POSTING_OPERATION_ID,
        "task_id": POSTING_TASK_ID,
        "actor_kind": "agent",
        "actor_id": "a-ui-native",
        "lock_operation_id": operation_id_for(
            "task-lock", (POSTING_TASK_ID, "a-ui-native")
        ),
        **POSTING_TERMS,
    }
)
POSTING_RECORD = {
    "record_version": 1,
    "scope": {
        "schema_version": 1,
        "operator_agent_id": "a-ui-native",
        "operator_public_fingerprint_sha256": "5" * 64,
        "service_urls": {
            "identity_url": "http://identity.test",
            "bank_url": "http://bank.test",
            "task_board_url": "http://board.test",
            "reputation_url": "http://reputation.test",
            "court_url": "http://court.test",
        },
    },
    "intent": _POSTING_INTENT.as_json(),
    "request_sha256": _POSTING_INTENT.request_sha256,
}


def _pending_posting_observation() -> dict[str, Any]:
    return {
        "observation": "submit",
        "status_code": 202,
        "publication": {
            "protocol_version": 1,
            "operation_id": POSTING_OPERATION_ID,
            "task_id": POSTING_TASK_ID,
            "request_sha256": _POSTING_INTENT.request_sha256,
            "publication_state": "awaiting_lock",
            "lock_operation_id": _POSTING_INTENT.lock_operation_id,
            "escrow_id": None,
            "admitted_at": "2026-10-19T09:00:00.000000Z",
            "published_at": None,
            "result": None,
            "error": None,
        },
    }


def _approval_response(operation_id: str, state: str) -> dict[str, Any]:
    completed = state == "completed"
    event_id = 51 if completed else None
    financial_request = {
        "escrow_id": "esc-native",
        "operation_kind": "escrow_release",
        "recipient_account_id": "a-worker",
        "request_version": 1,
        "source_reference": f"task:{TASK_ID}:approved",
    }
    encoded = json.dumps(financial_request, sort_keys=True, separators=(",", ":"))
    task_response = {
        "task_id": TASK_ID,
        "poster_id": "a-ui-native",
        "title": "Native UI task",
        "spec": "Keep the caller intent explicit.",
        "reward": 75,
        "bidding_deadline_seconds": 120,
        "deadline_seconds": 300,
        "review_deadline_seconds": 90,
        "status": "approved" if completed else "submitted",
        "escrow_id": "esc-native",
        "bid_count": 1,
        "worker_id": "a-worker",
        "accepted_bid_id": "bid-native",
        "created_at": "2026-10-18T11:58:00Z",
        "accepted_at": "2026-10-18T12:01:00Z",
        "submitted_at": "2026-10-18T12:03:00Z",
        "approved_at": "2026-10-18T12:04:00Z" if completed else None,
        "cancelled_at": None,
        "disputed_at": None,
        "dispute_reason": None,
        "dispute_id": None,
        "rebuttal_deadline": None,
        "rebuttal_submitted_at": None,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": False,
        "bidding_deadline": "2026-10-18T12:00:00Z",
        "acceptance_deadline": "2026-10-18T12:00:45Z",
        "execution_deadline": "2026-10-18T12:06:00Z",
        "review_deadline": "2026-10-18T12:04:30Z",
        "lifecycle_version": 8 if completed else 7,
        "timing_contract_version": 2,
        "acceptance_window_seconds": 45,
        "bidding_cutoff_us": 1_000_000,
        "acceptance_cutoff_us": 46_000_000,
        "execution_cutoff_us": 346_000_000,
        "review_cutoff_us": 436_000_000,
        "accepted_reward": 75,
        "accepted_bid_amount": 60,
        "pending_transition": None
        if completed
        else {
            "operation_id": operation_id,
            "command": "approve_task",
            "target_status": "approved",
            "target_lifecycle_version": 8,
            "operation_state": "claimed",
            "effect_state": "pending",
        },
        "legacy_reconciliations": [],
    }
    return {
        **task_response,
        "operation": {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": TASK_ID,
            "command": "approve_task",
            "request_sha256": "a" * 64,
            "operation_state": state,
            "source_status": "submitted",
            "source_lifecycle_version": 7,
            "target_status": "approved",
            "target_lifecycle_version": 8,
            "effect_kind": "bank_release",
            "effect_state": "succeeded" if completed else "pending",
            "effect_operation_id": operation_id,
            "effect_result_reference": operation_id if completed else None,
            "effect_intent": {
                "financial_request": financial_request,
                "financial_request_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                "expected_escrow": {
                    "escrow_id": "esc-native",
                    "task_id": TASK_ID,
                    "payer_account_id": "a-ui-native",
                    "amount": 75,
                },
            },
            "event_id": event_id,
            "result": None
            if not completed
            else {
                "task_id": TASK_ID,
                "command": "approve_task",
                "status": "approved",
                "lifecycle_version": 8,
                "event_id": event_id,
                "effect_result_reference": operation_id,
                "worker_id": "a-worker",
                "reward": 75,
            },
        },
    }


def _completed_response(
    command: str,
    operation_id: str,
    *,
    current_status: str,
) -> dict[str, Any]:
    if command == "approve_task":
        return _approval_response(operation_id, "completed")

    response = _approval_response(operation_id, "completed")
    response.update(
        {
            "status": current_status,
            "lifecycle_version": 9 if current_status == "submitted" else 8,
            "approved_at": None,
            "pending_transition": None,
        }
    )
    operation = response["operation"]
    operation.update(
        {
            "command": command,
            "source_status": "open" if command == "accept_bid" else "submitted",
            "target_status": "accepted" if command == "accept_bid" else "disputed",
            "effect_kind": "none" if command == "accept_bid" else "court_claim",
            "effect_state": "none" if command == "accept_bid" else "succeeded",
            "effect_operation_id": None,
            "effect_result_reference": None if command == "accept_bid" else "clm-native",
            "effect_intent": None,
        }
    )
    if command == "accept_bid":
        response.update(
            {
                "submitted_at": ("2026-10-18T12:03:00Z" if current_status == "submitted" else None),
                "review_deadline": (
                    "2026-10-18T12:04:30Z" if current_status == "submitted" else None
                ),
                "review_cutoff_us": 436_000_000 if current_status == "submitted" else None,
            }
        )
        operation["result"] = {
            "task_id": TASK_ID,
            "command": command,
            "status": "accepted",
            "lifecycle_version": 8,
            "event_id": 51,
            "bid_id": "bid-native",
            "worker_id": "a-worker",
            "reward": 75,
            "bid_amount": 60,
        }
    else:
        response.update(
            {
                "disputed_at": "2026-10-18T12:04:00Z",
                "dispute_reason": "Terms are incomplete.",
                "dispute_id": "clm-native",
            }
        )
        operation["effect_intent"] = {
            "court_claim": {
                "task_id": TASK_ID,
                "claimant_id": "a-ui-native",
                "respondent_id": "a-worker",
                "reason": "Terms are incomplete.",
            },
            "expected_escrow": {
                "escrow_id": "esc-native",
                "task_id": TASK_ID,
                "payer_account_id": "a-ui-native",
                "amount": 75,
            },
        }
        operation["result"] = {
            "task_id": TASK_ID,
            "command": command,
            "status": "disputed",
            "lifecycle_version": 8,
            "event_id": 51,
            "effect_result_reference": "clm-native",
            "dispute_id": "clm-native",
            "reason": "Terms are incomplete.",
            "worker_id": "a-worker",
        }
    return response


def _stored_intents(page: Page) -> list[dict[str, Any]]:
    return page.evaluate(
        """() => Object.values(sessionStorage).map(function(value) {
          try { return JSON.parse(value); } catch (_error) { return null; }
        }).filter(Boolean)"""
    )


def _install_ate_stub(page: Page) -> None:
    page.add_init_script(
        """
        window.ATE = {
          TASK_STATUS_BADGE: {open: {text: 'OPEN', cls: 'status-open'}},
          escapeHTML: function(value) { return String(value); },
          agentColor: function() { return '#00ffff'; },
          fetchMetrics: async function() { return {}; },
          fetchAgents: async function() { return []; },
          buildBottomTicker: function() {},
          startMetricsPolling: function() {},
          fetchEvents: async function() { return {events: []}; },
          mapEventToFeed: function() { return {badge: '', type: '', text: ''}; },
          connectSSE: function() { return {close: function() {}}; },
          proxyFetch: function(url, options) { return fetch(url, options); }
        };
        """
    )


def _page_route(route: Route) -> None:
    url = route.request.url
    if url.endswith("/api/proxy/browser-config"):
        route.fulfill(
            status=200,
            json={"csrf_header_name": "X-ATE-CSRF", "csrf_header_value": "local-operator"},
        )
        return
    if url.endswith("/assets/task-posting.js"):
        route.fulfill(
            status=200,
            content_type="application/javascript",
            body=TASK_POSTING_JS_PATH.read_text(),
        )
        return
    if url.endswith("/assets/task.js"):
        route.fulfill(
            status=200, content_type="application/javascript", body=TASK_JS_PATH.read_text()
        )
        return
    if url.startswith(TASK_URL):
        route.fulfill(status=200, content_type="text/html", body=PAGE_HTML)
        return
    route.abort()


def _mutation_path(action: str) -> str:
    if action == "accept_bid":
        return f"/api/proxy/tasks/{TASK_ID}/bids/bid-native/accept"
    if action == "approve_task":
        return f"/api/proxy/tasks/{TASK_ID}/approve"
    return f"/api/proxy/tasks/{TASK_ID}/dispute"


def _source_task_view(action: str) -> dict[str, Any]:
    return TASK_VIEW if action == "accept_bid" else SUBMITTED_TASK_VIEW


def _advanced_task_view(action: str) -> dict[str, Any]:
    return {
        "accept_bid": ACCEPTED_TASK_VIEW,
        "approve_task": APPROVED_TASK_VIEW,
        "dispute_task": DISPUTED_TASK_VIEW,
    }[action]


def _click_action(page: Page, action: str) -> None:
    if action == "accept_bid":
        expect(page.locator(".btn-accept-bid")).to_be_visible()
        page.locator(".btn-accept-bid").click()
    elif action == "approve_task":
        expect(page.locator("#btn-approve")).to_be_visible()
        page.locator("#btn-approve").click()
    else:
        expect(page.locator("#btn-dispute-show")).to_be_visible()
        page.locator("#btn-dispute-show").click()
        page.locator("#f-dispute-reason").fill("Terms are incomplete.")
        page.locator("#btn-submit-dispute").click()


class _RecoveryScenario:
    def __init__(self, action: str) -> None:
        self.action = action
        self.path = _mutation_path(action)
        self.mutation_bodies: list[str | None] = []
        self.task_reads: list[str] = []
        self.acknowledged = False

    def current_view(self) -> dict[str, Any]:
        if self.acknowledged and self.action == "accept_bid":
            return {
                **SUBMITTED_TASK_VIEW,
                "lifecycle_version": 9,
                "worker": {"agent_id": "a-worker", "name": "Worker"},
            }
        if self.mutation_bodies:
            return _advanced_task_view(self.action)
        return _source_task_view(self.action)

    def route(self, route: Route) -> None:
        request = route.request
        url = request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith(f"/api/tasks/{TASK_ID}"):
            self.task_reads.append(url)
            route.fulfill(status=200, json=self.current_view())
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/bids"):
            route.fulfill(status=200, json=AUTHORIZED_BIDS)
            return
        if url.endswith(self.path):
            self.mutation_bodies.append(request.post_data)
            if len(self.mutation_bodies) == 1:
                route.abort()
                return
            payload = json.loads(request.post_data or "{}")
            operation_id = payload.get("operation_id", FALLBACK_OPERATION_ID)
            current_status = (
                "submitted"
                if self.action == "accept_bid"
                else {
                    "approve_task": "approved",
                    "dispute_task": "disputed",
                }[self.action]
            )
            self.acknowledged = True
            route.fulfill(
                status=200,
                json=_completed_response(
                    self.action,
                    operation_id,
                    current_status=current_status,
                ),
            )
            return
        _page_route(route)


def test_create_form_requires_poster_selected_acceptance_window(page: Page) -> None:
    create_requests: list[dict[str, Any]] = []
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        url = route.request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith("/api/proxy/task-postings/prepare"):
            create_requests.append(route.request.post_data_json)
            route.fulfill(status=200, json=POSTING_RECORD)
            return
        if url.endswith(f"/api/proxy/task-postings/{POSTING_OPERATION_ID}/submit"):
            route.fulfill(status=202, json=_pending_posting_observation())
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(TASK_URL)

    expect(page.locator("#f-accept-window")).to_be_visible()
    assert page.locator("#f-accept-window").get_attribute("placeholder") == "Required"
    page.locator("#f-title").fill("Native UI task")
    page.locator("#f-spec").fill("Keep the caller intent explicit.")
    page.locator("#f-reward").fill("75")
    page.locator("#f-bid-dl").fill("120")
    page.locator("#f-accept-window").fill("45")
    page.locator("#f-exec-dl").fill("300")
    page.locator("#f-rev-dl").fill("90")
    with page.expect_request(
        lambda request: request.url.endswith("/api/proxy/task-postings/prepare")
    ):
        page.locator("#btn-post-task").click()

    assert len(create_requests) == 1
    assert create_requests[0]["acceptance_window_seconds"] == 45


@pytest.mark.parametrize("window", ["45.5", "0", "-1"])
def test_create_rejects_non_integral_or_nonpositive_acceptance_window(
    page: Page,
    window: str,
) -> None:
    create_requests: list[str | None] = []
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        url = route.request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith("/api/proxy/task-postings/prepare"):
            create_requests.append(route.request.post_data)
            route.fulfill(status=200, json=POSTING_RECORD)
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(TASK_URL)
    page.locator("#f-title").fill("Native UI task")
    page.locator("#f-spec").fill("Keep the caller intent explicit.")
    page.locator("#f-reward").fill("75")
    page.locator("#f-bid-dl").fill("120")
    page.locator("#f-accept-window").fill(window)
    page.locator("#f-exec-dl").fill("300")
    page.locator("#f-rev-dl").fill("90")
    page.locator("#btn-post-task").click()
    page.wait_for_timeout(100)

    assert create_requests == []
    expect(page.locator("#post-error")).to_contain_text(
        re.compile("positive|integer|required|whole", re.IGNORECASE)
    )


@pytest.mark.parametrize("action", ["accept_bid", "approve_task", "dispute_task"])
def test_every_action_freezes_identity_version_and_business_terms_before_transport(
    page: Page,
    action: str,
) -> None:
    mutation_bodies: list[str | None] = []
    path = _mutation_path(action)
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        request = route.request
        url = request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith(f"/api/tasks/{TASK_ID}"):
            route.fulfill(status=200, json=_source_task_view(action))
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/bids"):
            route.fulfill(status=200, json=AUTHORIZED_BIDS)
            return
        if url.endswith(path):
            mutation_bodies.append(request.post_data)
            route.abort()
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    with page.expect_request(lambda request: request.url.endswith(path)):
        _click_action(page, action)

    page.wait_for_function("sessionStorage.length > 0", timeout=1000)
    assert len(mutation_bodies) == 1
    assert mutation_bodies[0] is not None
    body = json.loads(mutation_bodies[0])
    intent = _stored_intents(page)[0]
    assert body["operation_id"] == intent["operation_id"]
    assert body["expected_lifecycle_version"] == intent["expected_lifecycle_version"] == 7
    assert intent["operator_agent_id"] == "a-ui-native"
    assert intent["task_id"] == TASK_ID
    assert intent["action"] == action
    if action == "accept_bid":
        assert intent["bid_id"] == "bid-native"
    elif action == "dispute_task":
        assert body["reason"] == intent["reason"] == "Terms are incomplete."


def test_pending_approval_reuses_frozen_intent_after_reload(page: Page) -> None:
    mutation_bodies: list[str | None] = []
    task_reads: list[str] = []
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        request = route.request
        url = request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith(f"/api/tasks/{TASK_ID}"):
            task_reads.append(url)
            route.fulfill(status=200, json=SUBMITTED_TASK_VIEW)
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve"):
            mutation_bodies.append(request.post_data)
            payload = json.loads(request.post_data or "{}")
            operation_id = payload.get("operation_id", FALLBACK_OPERATION_ID)
            route.fulfill(status=202, json=_approval_response(operation_id, "claimed"))
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    expect(page.locator("#btn-approve")).to_be_visible()
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve")
    ) as first_response:
        page.locator("#btn-approve").click()
    assert first_response.value.status == 202
    assert len(mutation_bodies) == 1
    page.wait_for_function("sessionStorage.length > 0", timeout=1000)

    page.reload()
    expect(page.locator("#btn-approve")).to_have_count(0)
    recovery = page.locator("[data-operation-recovery]")
    expect(recovery).to_be_visible()
    expect(recovery).to_be_enabled()
    assert len(mutation_bodies) == 1
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve")
    ) as second_response:
        recovery.click()
    assert second_response.value.status == 202
    assert len(mutation_bodies) == 2

    assert mutation_bodies[0] is not None
    assert mutation_bodies[1] == mutation_bodies[0]
    frozen_body = json.loads(mutation_bodies[0])
    assert frozen_body == {
        "operation_id": frozen_body["operation_id"],
        "expected_lifecycle_version": 7,
    }
    assert re.fullmatch(
        r"op-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        frozen_body["operation_id"],
    )
    assert len(task_reads) == 2
    expect(page.locator("body")).to_contain_text(re.compile("pending", re.IGNORECASE))
    expect(page.locator("[data-operation-rejection-reset]")).to_have_count(0)
    stored_intents = _stored_intents(page)
    assert len(stored_intents) == 1
    assert stored_intents[0]["operation_id"] == frozen_body["operation_id"]
    assert stored_intents[0]["expected_lifecycle_version"] == 7
    assert stored_intents[0]["action"] == "approve_task"
    assert stored_intents[0]["task_id"] == TASK_ID
    assert stored_intents[0]["operator_agent_id"] == "a-ui-native"


@pytest.mark.parametrize(
    "ack_kind",
    ["foreign", "malformed", "wrong_task", "wrong_command", "claimed_on_200"],
)
def test_invalid_completion_ack_retains_intent_without_success(
    page: Page,
    ack_kind: str,
) -> None:
    mutation_bodies: list[str | None] = []
    task_reads: list[str] = []
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        request = route.request
        url = request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith(f"/api/tasks/{TASK_ID}"):
            task_reads.append(url)
            route.fulfill(status=200, json=SUBMITTED_TASK_VIEW)
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve"):
            mutation_bodies.append(request.post_data)
            payload = json.loads(request.post_data or "{}")
            operation_id = payload.get("operation_id", FALLBACK_OPERATION_ID)
            if ack_kind == "foreign":
                body = _approval_response(FOREIGN_OPERATION_ID, "completed")
            elif ack_kind == "wrong_task":
                body = _approval_response(operation_id, "completed")
                body["operation"]["task_id"] = "t-different"
            elif ack_kind == "wrong_command":
                body = _approval_response(operation_id, "completed")
                body["operation"]["command"] = "dispute_task"
            elif ack_kind == "claimed_on_200":
                body = _approval_response(operation_id, "claimed")
            else:
                body = _approval_response(operation_id, "completed")
                body["operation"] = {
                    "operation_id": operation_id,
                    "command": "approve_task",
                }
            route.fulfill(status=200, json=body)
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    expect(page.locator("#btn-approve")).to_be_visible()
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve")
    ):
        page.locator("#btn-approve").click()

    assert len(mutation_bodies) == 1
    assert mutation_bodies[0] is not None
    frozen_body = json.loads(mutation_bodies[0])
    page.wait_for_function("sessionStorage.length > 0", timeout=1000)
    stored_intents = _stored_intents(page)
    assert len(stored_intents) == 1
    assert stored_intents[0]["operation_id"] == frozen_body["operation_id"]
    assert len(task_reads) == 1
    expect(page.locator("#task-status")).to_have_text("SUBMITTED")
    expect(page.locator("body")).to_contain_text(
        re.compile("acknowledgment|invalid|malformed|mismatch|response", re.IGNORECASE)
    )
    expect(page.locator("body")).not_to_contain_text("TASK LIFECYCLE COMPLETE")
    expect(page.locator("[data-operation-rejection-reset]")).to_have_count(0)


@pytest.mark.parametrize(
    ("status_code", "error_body"),
    [
        (
            409,
            {
                "error": "task_version_conflict",
                "message": "Task lifecycle version differs from the command precondition",
                "details": {
                    "expected_lifecycle_version": 7,
                    "actual_lifecycle_version": 8,
                    "current_status": "approved",
                },
            },
        ),
        (
            502,
            {
                "error": "task_approval_failed",
                "message": "Task Board returned a malformed error response",
                "details": {},
            },
        ),
        (
            409,
            {
                "error": "transition_pending",
                "message": "Another task transition already owns this task",
                "details": {"winning_operation_id": FOREIGN_OPERATION_ID},
            },
        ),
    ],
)
def test_proxy_error_retains_intent_without_refresh_or_success(
    page: Page,
    status_code: int,
    error_body: dict[str, Any],
) -> None:
    mutation_bodies: list[str | None] = []
    task_reads: list[str] = []
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        request = route.request
        url = request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith(f"/api/tasks/{TASK_ID}"):
            task_reads.append(url)
            route.fulfill(status=200, json=SUBMITTED_TASK_VIEW)
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve"):
            mutation_bodies.append(request.post_data)
            route.fulfill(status=status_code, json=error_body)
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve")
    ):
        page.locator("#btn-approve").click()

    page.wait_for_function("sessionStorage.length > 0", timeout=1000)
    assert len(mutation_bodies) == 1
    assert mutation_bodies[0] is not None
    operation_id = json.loads(mutation_bodies[0])["operation_id"]
    stored_intents = _stored_intents(page)
    assert len(stored_intents) == 1
    assert stored_intents[0]["operation_id"] == operation_id
    assert len(task_reads) == 1
    expect(page.locator("#task-status")).to_have_text("SUBMITTED")
    expect(page.locator("body")).to_contain_text(
        re.compile("conflict|failed|retry|response", re.IGNORECASE)
    )
    expect(page.locator("body")).not_to_contain_text("TASK LIFECYCLE COMPLETE")
    rejection_reset = page.locator("[data-operation-rejection-reset]")
    if error_body["error"] == "task_version_conflict":
        expect(rejection_reset).to_be_visible()
    else:
        expect(rejection_reset).to_have_count(0)


def test_only_matching_verified_completion_clears_intent_and_refreshes(page: Page) -> None:
    mutation_bodies: list[str | None] = []
    task_reads: list[str] = []
    completed = False
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        nonlocal completed
        request = route.request
        url = request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith(f"/api/tasks/{TASK_ID}"):
            task_reads.append(url)
            route.fulfill(
                status=200,
                json=APPROVED_TASK_VIEW if completed else SUBMITTED_TASK_VIEW,
            )
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve"):
            mutation_bodies.append(request.post_data)
            payload = json.loads(request.post_data or "{}")
            operation_id = payload.get("operation_id", FALLBACK_OPERATION_ID)
            completed = True
            route.fulfill(status=200, json=_approval_response(operation_id, "completed"))
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    expect(page.locator("#btn-approve")).to_be_visible()
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve")
    ):
        page.locator("#btn-approve").click()

    expect(page.locator("#task-status")).to_have_text("APPROVED")
    assert len(mutation_bodies) == 1
    assert mutation_bodies[0] is not None
    frozen_body = json.loads(mutation_bodies[0])
    assert re.fullmatch(
        r"op-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        frozen_body["operation_id"],
    )
    assert _stored_intents(page) == []
    assert len(task_reads) == 2


@pytest.mark.parametrize("action", ["accept_bid", "approve_task", "dispute_task"])
def test_recovery_surface_replays_frozen_intent_after_task_advances(
    page: Page,
    action: str,
) -> None:
    scenario = _RecoveryScenario(action)
    _install_ate_stub(page)
    page.route("http://ui.test/**", scenario.route)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    with page.expect_request(lambda request: request.url.endswith(scenario.path)):
        _click_action(page, action)
    page.wait_for_function("sessionStorage.length > 0", timeout=1000)

    page.reload()
    if action == "accept_bid":
        expect(page.locator(".btn-accept-bid")).to_have_count(0)
    elif action == "approve_task":
        expect(page.locator("#btn-approve")).to_have_count(0)
    else:
        expect(page.locator("#btn-dispute-show")).to_have_count(0)
    recovery = page.locator("[data-operation-recovery]")
    expect(recovery).to_be_visible()
    expect(recovery).to_be_enabled()
    assert len(scenario.mutation_bodies) == 1
    with page.expect_response(lambda response: response.url.endswith(scenario.path)):
        recovery.click()

    assert len(scenario.mutation_bodies) == 2
    assert scenario.mutation_bodies[0] is not None
    assert scenario.mutation_bodies[1] == scenario.mutation_bodies[0]
    original = json.loads(scenario.mutation_bodies[0])
    assert original["expected_lifecycle_version"] == 7
    if action == "dispute_task":
        assert original["reason"] == "Terms are incomplete."
    assert _stored_intents(page) == []
    assert len(scenario.task_reads) == 3


def test_recovery_intent_cannot_be_resigned_by_changed_operator(page: Page) -> None:
    mutation_bodies: list[str | None] = []
    operator_id = "a-ui-native"
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        request = route.request
        url = request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": operator_id})
            return
        if url.endswith(f"/api/tasks/{TASK_ID}"):
            task = APPROVED_TASK_VIEW if mutation_bodies else SUBMITTED_TASK_VIEW
            route.fulfill(status=200, json=task)
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve"):
            mutation_bodies.append(request.post_data)
            route.abort()
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    with page.expect_request(
        lambda request: request.url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve")
    ):
        page.locator("#btn-approve").click()
    page.wait_for_function("sessionStorage.length > 0", timeout=1000)

    operator_id = "a-different-operator"
    page.reload()
    recovery = page.locator("[data-operation-recovery]")
    expect(recovery).to_be_visible()
    expect(recovery).to_be_disabled()
    expect(page.locator("body")).to_contain_text(re.compile("operator|identity", re.IGNORECASE))
    assert len(mutation_bodies) == 1
    intent = _stored_intents(page)[0]
    assert intent["operator_agent_id"] == "a-ui-native"


def test_operator_can_acknowledge_definite_rejection_then_start_fresh_intent(page: Page) -> None:
    accept_bodies: list[str | None] = []
    approve_bodies: list[str | None] = []
    task_reads: list[str] = []
    _install_ate_stub(page)

    def route_request(route: Route) -> None:
        request = route.request
        url = request.url
        if url.endswith("/api/proxy/identity"):
            route.fulfill(status=200, json={"agent_id": "a-ui-native"})
            return
        if url.endswith(f"/api/tasks/{TASK_ID}"):
            task_reads.append(url)
            current = (
                TASK_VIEW
                if len(task_reads) == 1
                else {
                    **SUBMITTED_TASK_VIEW,
                    "lifecycle_version": 9,
                    "worker": {"agent_id": "a-worker", "name": "Worker"},
                }
            )
            route.fulfill(status=200, json=current)
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/bids"):
            route.fulfill(status=200, json=AUTHORIZED_BIDS)
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/bids/bid-native/accept"):
            accept_bodies.append(request.post_data)
            route.fulfill(
                status=409,
                json={
                    "error": "task_version_conflict",
                    "message": "Task lifecycle version differs from the command precondition",
                    "details": {
                        "expected_lifecycle_version": 7,
                        "actual_lifecycle_version": 9,
                        "current_status": "submitted",
                    },
                },
            )
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve"):
            approve_bodies.append(request.post_data)
            route.abort()
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/proxy/tasks/{TASK_ID}/bids/bid-native/accept")
    ):
        page.locator(".btn-accept-bid").click()

    assert len(task_reads) == 1
    assert accept_bodies[0] is not None
    rejected = json.loads(accept_bodies[0])
    reset = page.locator("[data-operation-rejection-reset]")
    expect(reset).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith(f"/api/tasks/{TASK_ID}")):
        reset.click()

    assert _stored_intents(page) == []
    expect(page.locator("#task-status")).to_have_text("SUBMITTED")
    expect(page.locator("#btn-approve")).to_be_visible()
    with page.expect_request(
        lambda request: request.url.endswith(f"/api/proxy/tasks/{TASK_ID}/approve")
    ):
        page.locator("#btn-approve").click()

    assert approve_bodies[0] is not None
    fresh = json.loads(approve_bodies[0])
    assert fresh["operation_id"] != rejected["operation_id"]
    assert fresh["expected_lifecycle_version"] == 9
    intent = _stored_intents(page)[0]
    assert intent["action"] == "approve_task"
    assert intent["operator_agent_id"] == "a-ui-native"

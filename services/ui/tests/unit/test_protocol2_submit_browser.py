"""Browser contract for governed operator submissions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from playwright.sync_api import expect
from test_mp07_native_proxy_browser import (
    ACCEPTED_TASK_VIEW,
    TASK_ID,
    TASK_URL,
    _install_ate_stub,
    _page_route,
    _stored_intents,
    native_browser,
    page,
)

if TYPE_CHECKING:
    from playwright.sync_api import Page, Route

pytestmark = pytest.mark.unit

__all__ = ["native_browser", "page"]


def _accepted_operator_view() -> dict[str, Any]:
    return {
        **ACCEPTED_TASK_VIEW,
        "worker": {"agent_id": "a-ui-native", "name": "Operator"},
        "assets": [
            {
                "asset_id": "asset-operator-output",
                "uploader_id": "a-ui-native",
                "filename": "answer.txt",
                "content_type": "text/plain",
                "size_bytes": 9,
                "content_hash": "b" * 64,
                "uploaded_at": "2026-10-18T12:02:00Z",
                "role": "output",
            }
        ],
    }


def _submitted_operator_view() -> dict[str, Any]:
    return {
        **_accepted_operator_view(),
        "status": "submitted",
        "lifecycle_version": 9,
        "submitted_at": "2026-10-18T12:03:00Z",
    }


def _protocol2_submission_response(operation_id: str, worker_id: str) -> dict[str, Any]:
    return {
        "task_id": TASK_ID,
        "status": "submitted",
        "lifecycle_version": 9,
        "operation": {
            "protocol_version": 2,
            "operation_id": operation_id,
            "task_id": TASK_ID,
            "command": "submit_deliverable",
            "request_sha256": "a" * 64,
            "operation_state": "completed",
            "source_status": "accepted",
            "source_lifecycle_version": 8,
            "target_status": "submitted",
            "target_lifecycle_version": 9,
            "effect_kind": "none",
            "effect_state": "none",
            "effect_operation_id": None,
            "effect_result_reference": None,
            "effect_intent": None,
            "event_id": 61,
            "result": {
                "task_id": TASK_ID,
                "command": "submit_deliverable",
                "status": "submitted",
                "lifecycle_version": 9,
                "event_id": 61,
                "worker_id": worker_id,
                "manifest_id": "manifest-output",
                "manifest_sha256": "b" * 64,
            },
        },
    }


@pytest.mark.parametrize(
    ("worker_id", "accepted"),
    [("a-ui-native", True), ("a-different-worker", False)],
)
def test_protocol2_submission_acknowledgment_binds_operator_and_manifest(
    page: Page,
    worker_id: str,
    accepted: bool,
) -> None:
    mutation_bodies: list[str | None] = []
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
            route.fulfill(
                status=200,
                json=_submitted_operator_view() if completed else _accepted_operator_view(),
            )
            return
        if url.endswith(f"/api/proxy/tasks/{TASK_ID}/submit"):
            mutation_bodies.append(request.post_data)
            payload = json.loads(request.post_data or "{}")
            completed = accepted
            route.fulfill(
                status=200,
                json=_protocol2_submission_response(payload["operation_id"], worker_id),
            )
            return
        _page_route(route)

    page.route("http://ui.test/**", route_request)
    page.goto(f"{TASK_URL}?task_id={TASK_ID}")
    expect(page.locator("#btn-submit-deliverable")).to_be_visible()
    with page.expect_response(
        lambda response: response.url.endswith(f"/api/proxy/tasks/{TASK_ID}/submit")
    ):
        page.locator("#btn-submit-deliverable").click()

    assert len(mutation_bodies) == 1
    if accepted:
        expect(page.locator("#task-status")).to_have_text("SUBMITTED")
        assert _stored_intents(page) == []
        expect(page.locator("[data-operation-recovery]")).to_have_count(0)
    else:
        expect(page.locator("#task-status")).to_have_text("ACCEPTED")
        intents = _stored_intents(page)
        assert len(intents) == 1
        assert intents[0]["operator_agent_id"] == "a-ui-native"

"""Coverage for the operator write path (``/api/proxy/*``), previously
untested anywhere in the repo — confirmed via a repo-wide grep across
``services/ui/tests/`` (including ``tests/e2e/``) and the root ``tests/``
tree before writing this file: zero hits for "proxy" outside this service's
own source and the T-072 spec document itself.

Spec IDs (docs/specifications/service-tests/ui-service-tests.md):
Category 10 (PROXY-01..10), Category 11 (VAL-01, VAL-04), Category 13
(SEC-02).

``ui_service.services.proxy`` is a thin pass-through: it fetches the
app-state ``UserAgent``, calls exactly one method on it, and error-maps any
exception to a ``ServiceError``. These tests exercise precisely that
UI-owned contract (parameter forwarding, response passthrough, and
error-code mapping) via a locally-defined fake ``UserAgent`` installed
directly on app state — the same technique already used by
``tests/unit/test_treasury_funding.py`` and
``tests/unit/test_lifespan_logging.py`` to avoid depending on a live
Identity/Task Board service.

Not re-verified here: the downstream economic *effect* performed by the
real Task Board / Central Bank (escrow actually locked from the operator's
account, an actual task-status transition on Task Board's own ledger) — that
belongs to Task Board's own test suite and to live multi-service
integration/e2e, not to a UI-service-isolated harness with a faked
UserAgent.
"""

from __future__ import annotations

from typing import Any

import pytest

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration


class _FakeUserAgent:
    """Minimal stand-in for ``service_auth.UserAgent``'s proxy-relevant surface."""

    def __init__(self, agent_id: str = "a-operator-fake") -> None:
        self.agent_id = agent_id
        self.post_task_calls: list[dict[str, Any]] = []
        self.accept_bid_calls: list[dict[str, Any]] = []
        self.approve_task_calls: list[dict[str, Any]] = []
        self.dispute_task_calls: list[dict[str, Any]] = []
        self.post_task_result: dict[str, Any] = {
            "task_id": "t-fake-1",
            "poster_id": agent_id,
            "status": "open",
        }
        self.post_task_raises: Exception | None = None

    async def post_task(self, **kwargs: Any) -> dict[str, Any]:
        self.post_task_calls.append(kwargs)
        if self.post_task_raises is not None:
            raise self.post_task_raises
        return self.post_task_result

    async def accept_bid(self, **kwargs: Any) -> dict[str, Any]:
        self.accept_bid_calls.append(kwargs)
        return {"task_id": kwargs["task_id"], "status": "accepted"}

    async def approve_task(self, **kwargs: Any) -> dict[str, Any]:
        self.approve_task_calls.append(kwargs)
        return {"task_id": kwargs["task_id"], "status": "approved"}

    async def dispute_task(self, **kwargs: Any) -> dict[str, Any]:
        self.dispute_task_calls.append(kwargs)
        return {"task_id": kwargs["task_id"], "status": "disputed"}

    async def close(self) -> None:
        """Required so lifespan shutdown (``await state.user_agent.close()``) is a no-op."""
        return None


VALID_CREATE_BODY: dict[str, Any] = {
    "title": "A new task",
    "spec": "Do the thing",
    "reward": 50,
    "bidding_deadline_seconds": 3600,
    "execution_deadline_seconds": 7200,
    "review_deadline_seconds": 3600,
}


async def test_identity_returns_operator_agent_id(client) -> None:
    """PROXY-01: GET /api/proxy/identity returns the UserAgent's agent_id."""
    fake = _FakeUserAgent(agent_id="a-operator-under-test")
    get_app_state().user_agent = fake

    response = await client.get("/api/proxy/identity")
    assert response.status_code == 200
    assert response.json()["agent_id"] == "a-operator-under-test"


async def test_identity_unavailable_when_user_agent_missing(client) -> None:
    """PROXY-02: no UserAgent initialized -> 503 user_agent_unavailable."""
    get_app_state().user_agent = None

    response = await client.get("/api/proxy/identity")
    assert response.status_code == 503
    assert response.json()["error"] == "user_agent_unavailable"


async def test_create_task_passthrough_no_auth_header_required(client) -> None:
    """PROXY-03 (passthrough) + SEC-02: a valid create-task call with a
    working UserAgent succeeds with no auth header of any kind, and the
    fields are forwarded to UserAgent.post_task unchanged."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post("/api/proxy/tasks", json=VALID_CREATE_BODY)
    assert response.status_code == 200
    assert response.json() == fake.post_task_result
    assert fake.post_task_calls == [VALID_CREATE_BODY]


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "x" * 201},
        {"reward": 0},
        {"spec": "x" * 10001},
    ],
)
async def test_create_task_field_bounds_rejected_with_422(
    client, overrides: dict[str, Any]
) -> None:
    """PROXY-04: title>200 chars, reward=0, spec>10000 chars are all 422
    before the UserAgent is ever consulted; no task is created."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake
    body = {**VALID_CREATE_BODY, **overrides}

    response = await client.post("/api/proxy/tasks", json=body)
    assert response.status_code == 422
    assert fake.post_task_calls == []


async def test_create_task_downstream_failure_surfaces_as_502(client) -> None:
    """PROXY-05: a UserAgent.post_task failure maps to 502 task_creation_failed."""
    fake = _FakeUserAgent()
    fake.post_task_raises = RuntimeError("Task Board unreachable")
    get_app_state().user_agent = fake

    response = await client.post("/api/proxy/tasks", json=VALID_CREATE_BODY)
    assert response.status_code == 502
    assert response.json()["error"] == "task_creation_failed"


async def test_accept_bid_passthrough(client) -> None:
    """PROXY-06: accept-bid forwards task_id/bid_id and passes through the result."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post("/api/proxy/tasks/t-task2/bids/bid-3/accept")
    assert response.status_code == 200
    assert response.json() == {"task_id": "t-task2", "status": "accepted"}
    assert fake.accept_bid_calls == [{"task_id": "t-task2", "bid_id": "bid-3"}]


async def test_approve_task_passthrough(client) -> None:
    """PROXY-07: approve forwards task_id and passes through the result."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post("/api/proxy/tasks/t-task8/approve")
    assert response.status_code == 200
    assert response.json() == {"task_id": "t-task8", "status": "approved"}
    assert fake.approve_task_calls == [{"task_id": "t-task8"}]


async def test_file_dispute_passthrough(client) -> None:
    """PROXY-08: dispute forwards task_id/reason and passes through the result."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post(
        "/api/proxy/tasks/t-task8/dispute", json={"reason": "Not delivered"}
    )
    assert response.status_code == 200
    assert response.json() == {"task_id": "t-task8", "status": "disputed"}
    assert fake.dispute_task_calls == [{"task_id": "t-task8", "reason": "Not delivered"}]


@pytest.mark.parametrize("reason", ["", "x" * 5001])
async def test_dispute_reason_validation_rejected_with_422(client, reason: str) -> None:
    """PROXY-09: empty or >5000-char reason is 422; no dispute filed."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post("/api/proxy/tasks/t-task8/dispute", json={"reason": reason})
    assert response.status_code == 422
    assert fake.dispute_task_calls == []


async def test_create_task_extra_field_rejected_with_422(client) -> None:
    """PROXY-10: an unexpected top-level field is 422 (extra=forbid on CreateTaskRequest)."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake
    body = {**VALID_CREATE_BODY, "unexpected_field": "nope"}

    response = await client.post("/api/proxy/tasks", json=body)
    assert response.status_code == 422
    assert fake.post_task_calls == []


async def test_non_json_content_type_rejected_with_415(client) -> None:
    """VAL-01: wrong Content-Type on POST /api/proxy/tasks -> 415, rejected
    by the middleware before the route handler (and therefore the
    UserAgent) ever runs."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post(
        "/api/proxy/tasks",
        content=b"not json",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"
    assert fake.post_task_calls == []


async def test_bodyless_approve_route_not_subject_to_json_validation(client) -> None:
    """VAL-04: POST .../approve with no body and no Content-Type header is
    never 415/413 — it is not one of the JSON-validated routes at all. A
    503 downstream (no UserAgent) is an acceptable outcome; 415/413 is not."""
    get_app_state().user_agent = None

    response = await client.post("/api/proxy/tasks/t-task8/approve")
    assert response.status_code not in (415, 413)

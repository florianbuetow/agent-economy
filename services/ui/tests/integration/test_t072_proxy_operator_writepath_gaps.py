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

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.financial_identity import operation_id_for
from service_auth.jws import TokenPolicy
from service_auth.task_board_responses import TaskBoardCommandResponse
from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration

OPERATION_ID = "op-00000000-0000-4000-8000-000000000072"
POSTING_OPERATION_ID = "op-00000000-0000-4000-8000-000000000073"
POSTING_TASK_ID = "t-00000000-0000-4000-8000-000000000074"
NATIVE_IDENTITY = {
    "operation_id": OPERATION_ID,
    "expected_lifecycle_version": 7,
}


def _mutation_result(command: str, task_id: str) -> dict[str, Any]:
    source_status, target_status, effect_kind = {
        "accept_bid": ("open", "accepted", "none"),
        "approve_task": ("submitted", "approved", "bank_release"),
        "dispute_task": ("submitted", "disputed", "court_claim"),
    }[command]
    effect_reference = (
        OPERATION_ID
        if command == "approve_task"
        else ("clm-native" if command == "dispute_task" else None)
    )
    return {
        "task_id": task_id,
        "status": target_status,
        "lifecycle_version": 8,
        "operation": {
            "protocol_version": 1,
            "operation_id": OPERATION_ID,
            "task_id": task_id,
            "command": command,
            "request_sha256": "a" * 64,
            "operation_state": "completed",
            "source_status": source_status,
            "source_lifecycle_version": 7,
            "target_status": target_status,
            "target_lifecycle_version": 8,
            "effect_kind": effect_kind,
            "effect_state": "none" if effect_kind == "none" else "succeeded",
            "effect_operation_id": OPERATION_ID if command == "approve_task" else None,
            "effect_result_reference": effect_reference,
            "effect_intent": None if effect_kind == "none" else {"fixture": command},
            "event_id": 72,
            "result": {
                "task_id": task_id,
                "command": command,
                "status": target_status,
                "lifecycle_version": 8,
                "event_id": 72,
            },
        },
    }


class _FakeUserAgent:
    """Minimal stand-in for ``service_auth.UserAgent``'s proxy-relevant surface."""

    def __init__(self, agent_id: str = "a-operator-fake") -> None:
        self.agent_id = agent_id
        private_key = Ed25519PrivateKey.generate()
        self.config = AgentConfig(
            name="UI operator",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="https://identity.test",
            bank_url="https://bank.test",
            task_board_url="https://board.test",
            reputation_url="https://reputation.test",
            court_url="https://court.test",
            token_ttl_seconds=30,
            token_policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
        )
        self.prepare_task_posting_calls: list[dict[str, Any]] = []
        self.submit_task_posting_calls: list[TaskPostingIntent] = []
        self.accept_bid_calls: list[dict[str, Any]] = []
        self.approve_task_calls: list[dict[str, Any]] = []
        self.dispute_task_calls: list[dict[str, Any]] = []
        self.submit_task_posting_raises: Exception | None = None

    def prepare_task_posting(self, **kwargs: Any) -> TaskPostingIntent:
        self.prepare_task_posting_calls.append(kwargs)
        return TaskPostingIntent.from_json(
            {
                "protocol_version": 1,
                "operation_id": POSTING_OPERATION_ID,
                "task_id": POSTING_TASK_ID,
                "actor_kind": "agent",
                "actor_id": self.agent_id,
                "lock_operation_id": operation_id_for(
                    "task-lock", (POSTING_TASK_ID, self.agent_id)
                ),
                **kwargs,
            }
        )

    async def submit_task_posting(self, intent: TaskPostingIntent) -> TaskPostingResponse:
        self.submit_task_posting_calls.append(intent)
        if self.submit_task_posting_raises is not None:
            raise self.submit_task_posting_raises
        admitted = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
        published = admitted + timedelta(seconds=1)
        bidding = published + timedelta(seconds=intent.bidding_deadline_seconds)
        acceptance = bidding + timedelta(seconds=intent.acceptance_window_seconds)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        return TaskPostingResponse(
            status_code=201,
            body={
                "protocol_version": 1,
                "operation_id": intent.operation_id,
                "task_id": intent.task_id,
                "request_sha256": intent.request_sha256,
                "publication_state": "published",
                "lock_operation_id": intent.lock_operation_id,
                "escrow_id": "esc-fake-1",
                "admitted_at": admitted.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "published_at": published.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "result": {
                    "task_id": intent.task_id,
                    "poster_id": intent.actor_id,
                    "title": intent.title,
                    "spec": intent.spec,
                    "reward": intent.reward,
                    "bidding_deadline_seconds": intent.bidding_deadline_seconds,
                    "acceptance_window_seconds": intent.acceptance_window_seconds,
                    "execution_deadline_seconds": intent.execution_deadline_seconds,
                    "review_deadline_seconds": intent.review_deadline_seconds,
                    "status": "open",
                    "escrow_id": "esc-fake-1",
                    "created_at": published.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "bidding_deadline": bidding.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "acceptance_deadline": acceptance.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "bidding_cutoff_us": int((bidding - epoch).total_seconds() * 1_000_000),
                    "acceptance_cutoff_us": int((acceptance - epoch).total_seconds() * 1_000_000),
                    "lifecycle_version": 0,
                    "timing_contract_version": 2,
                    "event_id": 73,
                },
                "error": None,
            },
        )

    async def accept_bid(self, **kwargs: Any) -> TaskBoardCommandResponse:
        self.accept_bid_calls.append(kwargs)
        return TaskBoardCommandResponse(
            status_code=200,
            body=_mutation_result("accept_bid", kwargs["task_id"]),
        )

    async def approve_task(self, **kwargs: Any) -> TaskBoardCommandResponse:
        self.approve_task_calls.append(kwargs)
        return TaskBoardCommandResponse(
            status_code=200,
            body=_mutation_result("approve_task", kwargs["task_id"]),
        )

    async def dispute_task(self, **kwargs: Any) -> TaskBoardCommandResponse:
        self.dispute_task_calls.append(kwargs)
        return TaskBoardCommandResponse(
            status_code=200,
            body=_mutation_result("dispute_task", kwargs["task_id"]),
        )

    async def close(self) -> None:
        """Required so lifespan shutdown (``await state.user_agent.close()``) is a no-op."""
        return None


VALID_CREATE_BODY: dict[str, Any] = {
    "title": "A new task",
    "spec": "Do the thing",
    "reward": 50,
    "bidding_deadline_seconds": 3600,
    "acceptance_window_seconds": 1800,
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
    """PROXY-03 + SEC-02: prepare and submit need no browser auth header."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    prepared = await client.post("/api/proxy/task-postings/prepare", json=VALID_CREATE_BODY)
    assert prepared.status_code == 200
    operation_id = prepared.json()["intent"]["operation_id"]
    response = await client.post(
        f"/api/proxy/task-postings/{operation_id}/submit", json=prepared.json()
    )
    assert response.status_code == 201
    assert response.json()["publication"]["result"]["poster_id"] == fake.agent_id
    assert response.json()["publication"]["result"]["acceptance_window_seconds"] == 1800
    assert fake.prepare_task_posting_calls == [VALID_CREATE_BODY]
    assert fake.submit_task_posting_calls == [
        TaskPostingIntent.from_json(prepared.json()["intent"])
    ]


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

    response = await client.post("/api/proxy/task-postings/prepare", json=body)
    assert response.status_code == 422
    assert fake.prepare_task_posting_calls == []
    assert fake.submit_task_posting_calls == []


async def test_create_task_downstream_failure_surfaces_as_502(client) -> None:
    """PROXY-05: a native submit failure maps to 502 task_posting_failed."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    prepared = await client.post("/api/proxy/task-postings/prepare", json=VALID_CREATE_BODY)
    assert prepared.status_code == 200
    fake.submit_task_posting_raises = RuntimeError("Task Board unreachable")
    response = await client.post(
        f"/api/proxy/task-postings/{POSTING_OPERATION_ID}/submit", json=prepared.json()
    )
    assert response.status_code == 502
    assert response.json()["error"] == "task_posting_failed"


async def test_accept_bid_passthrough(client) -> None:
    """PROXY-06: accept-bid forwards task_id/bid_id and passes through the result."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post(
        "/api/proxy/tasks/t-task2/bids/bid-3/accept",
        json=NATIVE_IDENTITY,
    )
    assert response.status_code == 200
    assert response.json() == _mutation_result("accept_bid", "t-task2")
    assert fake.accept_bid_calls == [{"task_id": "t-task2", "bid_id": "bid-3", **NATIVE_IDENTITY}]


async def test_approve_task_passthrough(client) -> None:
    """PROXY-07: approve forwards task_id and passes through the result."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post("/api/proxy/tasks/t-task8/approve", json=NATIVE_IDENTITY)
    assert response.status_code == 200
    assert response.json() == _mutation_result("approve_task", "t-task8")
    assert fake.approve_task_calls == [{"task_id": "t-task8", **NATIVE_IDENTITY}]


async def test_file_dispute_passthrough(client) -> None:
    """PROXY-08: dispute forwards task_id/reason and passes through the result."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post(
        "/api/proxy/tasks/t-task8/dispute",
        json={"reason": "Not delivered", **NATIVE_IDENTITY},
    )
    assert response.status_code == 200
    assert response.json() == _mutation_result("dispute_task", "t-task8")
    assert fake.dispute_task_calls == [
        {"task_id": "t-task8", "reason": "Not delivered", **NATIVE_IDENTITY}
    ]


@pytest.mark.parametrize("reason", ["", "x" * 5001])
async def test_dispute_reason_validation_rejected_with_422(client, reason: str) -> None:
    """PROXY-09: empty or >5000-char reason is 422; no dispute filed."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post(
        "/api/proxy/tasks/t-task8/dispute",
        json={"reason": reason, **NATIVE_IDENTITY},
    )
    assert response.status_code == 422
    assert fake.dispute_task_calls == []


async def test_create_task_extra_field_rejected_with_422(client) -> None:
    """PROXY-10: an unexpected top-level field is 422 (extra=forbid on CreateTaskRequest)."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake
    body = {**VALID_CREATE_BODY, "unexpected_field": "nope"}

    response = await client.post("/api/proxy/task-postings/prepare", json=body)
    assert response.status_code == 422
    assert fake.prepare_task_posting_calls == []
    assert fake.submit_task_posting_calls == []


async def test_non_json_content_type_rejected_with_415(client) -> None:
    """VAL-01: wrong Content-Type on posting prepare -> 415, rejected
    by the middleware before the route handler (and therefore the
    UserAgent) ever runs."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post(
        "/api/proxy/task-postings/prepare",
        content=b"not json",
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"
    assert fake.prepare_task_posting_calls == []
    assert fake.submit_task_posting_calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/proxy/tasks/t-task8/approve",
        "/api/proxy/tasks/t-task2/bids/bid-3/accept",
    ],
)
@pytest.mark.parametrize("content_type", [None, "text/plain"])
async def test_native_mutation_requires_json_content_type(
    client,
    path: str,
    content_type: str | None,
) -> None:
    """Accept/approve JSON bodies are rejected at middleware before the operator."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake
    headers = {} if content_type is None else {"Content-Type": content_type}

    response = await client.post(path, content=b"{}", headers=headers)

    assert response.status_code == 415
    assert response.json()["error"] == "unsupported_media_type"
    assert fake.accept_bid_calls == []
    assert fake.approve_task_calls == []


@pytest.mark.parametrize(
    "path",
    [
        "/api/proxy/tasks/t-task8/approve",
        "/api/proxy/tasks/t-task2/bids/bid-3/accept",
    ],
)
async def test_native_mutation_empty_json_body_is_422_before_operator(client, path: str) -> None:
    """A valid JSON media type does not make an absent required body valid."""
    fake = _FakeUserAgent()
    get_app_state().user_agent = fake

    response = await client.post(
        path,
        content=b"",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert fake.accept_bid_calls == []
    assert fake.approve_task_calls == []

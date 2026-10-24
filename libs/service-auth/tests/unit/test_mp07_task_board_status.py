"""MP-07 public Task Board commands preserve HTTP success status."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig
from service_auth.jws import TokenPolicy, parse_jws

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.unit

_OPERATION_ID = "op-00000000-0000-0000-0000-000000000001"
_ERROR_BODY = {
    "error": "task_version_conflict",
    "message": "Task lifecycle version differs from the command precondition",
    "details": {
        "expected_lifecycle_version": 7,
        "actual_lifecycle_version": 8,
        "current_status": "accepted",
    },
}


def _config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name="Alice",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url="https://identity",
        bank_url="https://bank",
        task_board_url="https://board",
        reputation_url="https://reputation",
        court_url="https://court",
        token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


async def _agent(handler: Callable[[httpx.Request], httpx.Response]) -> BaseAgent:
    agent = BaseAgent(_config())
    await agent._http.aclose()
    agent.agent_id = "a-alice"
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return agent


async def _call(agent: BaseAgent, method: str, args: tuple[object, ...]) -> Any:
    producer = getattr(agent, method)
    return await producer(
        *args,
        operation_id=_OPERATION_ID,
        expected_lifecycle_version=7,
    )


def _completed_body(effect_kind: str) -> dict[str, object]:
    return {
        "task_id": "t-1",
        "status": "approved" if effect_kind != "none" else "accepted",
        "lifecycle_version": 8,
        "operation": {
            "operation_state": "completed",
            "effect_kind": effect_kind,
            "effect_state": "succeeded" if effect_kind != "none" else "none",
        },
    }


def _pending_body(effect_kind: str) -> dict[str, object]:
    return {
        "task_id": "t-1",
        "status": "submitted",
        "lifecycle_version": 7,
        "operation": {
            "operation_state": "claimed",
            "effect_kind": effect_kind,
            "effect_state": "pending",
        },
    }


def _bid_body() -> dict[str, object]:
    return {
        "bid_id": "bid-00000000-0000-0000-0000-000000000001",
        "task_id": "t-1",
        "bidder_id": "a-alice",
        "amount": 75,
        "submitted_at": "2026-10-18T10:00:00Z",
    }


def _token(request: httpx.Request) -> Any:
    return parse_jws(json.loads(request.content)["token"])


async def _assert_contract_error(
    agent: BaseAgent,
    method: str,
    args: tuple[object, ...],
    expected_status_code: int,
) -> None:
    with pytest.raises(Exception) as exc_info:
        await _call(agent, method, args)
    exc = exc_info.value
    assert type(exc).__name__ == "TaskBoardCommandContractError"
    assert getattr(exc, "status_code", None) == expected_status_code
    reason = getattr(exc, "reason", None)
    assert isinstance(reason, str)
    assert reason


def test_public_status_types_are_constructible_and_response_is_frozen() -> None:
    module = importlib.import_module("service_auth.task_board_responses")
    response_type = module.TaskBoardCommandResponse
    error_type = module.TaskBoardCommandContractError

    response = response_type(status_code=200, body={"task_id": "t-1"})
    assert response.status_code == 200
    assert response.body == {"task_id": "t-1"}
    with pytest.raises(AttributeError):
        response.status_code = 202

    error = error_type(status_code=202, reason="claimed operation requires retry")
    assert error.status_code == 202
    assert error.reason == "claimed operation requires retry"
    assert isinstance(error, Exception)


@pytest.mark.parametrize(
    ("method", "args", "status_code", "body", "expected_payload", "resource"),
    [
        (
            "submit_bid",
            ("t-1", 75),
            201,
            _bid_body(),
            {"action": "submit_bid", "task_id": "t-1", "bidder_id": "a-alice", "amount": 75},
            "POST /tasks/t-1/bids",
        ),
        (
            "accept_bid",
            ("t-1", "bid-1"),
            200,
            _completed_body("none"),
            {
                "action": "accept_bid",
                "task_id": "t-1",
                "bid_id": "bid-1",
                "poster_id": "a-alice",
            },
            "POST /tasks/t-1/bids/bid-1/accept",
        ),
        (
            "submit_deliverable",
            ("t-1",),
            200,
            _completed_body("none"),
            {"action": "submit_deliverable", "task_id": "t-1", "worker_id": "a-alice"},
            "POST /tasks/t-1/submit",
        ),
        (
            "cancel_task",
            ("t-1",),
            200,
            _completed_body("bank_release"),
            {"action": "cancel_task", "task_id": "t-1", "poster_id": "a-alice"},
            "POST /tasks/t-1/cancel",
        ),
        (
            "cancel_task",
            ("t-1",),
            202,
            _pending_body("bank_release"),
            {"action": "cancel_task", "task_id": "t-1", "poster_id": "a-alice"},
            "POST /tasks/t-1/cancel",
        ),
        (
            "approve_task",
            ("t-1",),
            200,
            _completed_body("bank_release"),
            {"action": "approve_task", "task_id": "t-1", "poster_id": "a-alice"},
            "POST /tasks/t-1/approve",
        ),
        (
            "approve_task",
            ("t-1",),
            202,
            _pending_body("bank_release"),
            {"action": "approve_task", "task_id": "t-1", "poster_id": "a-alice"},
            "POST /tasks/t-1/approve",
        ),
        (
            "dispute_task",
            ("t-1", "Specification was not met"),
            200,
            _completed_body("court_claim"),
            {
                "action": "dispute_task",
                "task_id": "t-1",
                "poster_id": "a-alice",
                "reason": "Specification was not met",
            },
            "POST /tasks/t-1/dispute",
        ),
        (
            "dispute_task",
            ("t-1", "Specification was not met"),
            202,
            _pending_body("court_claim"),
            {
                "action": "dispute_task",
                "task_id": "t-1",
                "poster_id": "a-alice",
                "reason": "Specification was not met",
            },
            "POST /tasks/t-1/dispute",
        ),
    ],
)
async def test_native_command_returns_status_and_preserves_signed_fields(
    method: str,
    args: tuple[object, ...],
    status_code: int,
    body: dict[str, object],
    expected_payload: dict[str, object],
    resource: str,
) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status_code, json=body, request=request)

    agent = await _agent(handler)
    try:
        result = await _call(agent, method, args)
    finally:
        await agent.close()

    assert type(result).__name__ == "TaskBoardCommandResponse"
    assert result.status_code == status_code
    assert result.body == body
    assert len(captured) == 1
    token = _token(captured[0])
    assert token.header.aud == "task-board"
    assert token.header.resource == resource
    assert token.payload == {
        **expected_payload,
        "operation_id": _OPERATION_ID,
        "expected_lifecycle_version": 7,
    }


@pytest.mark.parametrize(
    ("method", "args", "status_code", "body"),
    [
        ("submit_bid", ("t-1", 75), 200, _bid_body()),
        ("accept_bid", ("t-1", "bid-1"), 201, _completed_body("none")),
        ("submit_deliverable", ("t-1",), 202, _pending_body("none")),
        ("cancel_task", ("t-1",), 201, _completed_body("bank_release")),
        ("approve_task", ("t-1",), 201, _completed_body("bank_release")),
        (
            "dispute_task",
            ("t-1", "Specification was not met"),
            201,
            _completed_body("court_claim"),
        ),
    ],
)
async def test_native_command_rejects_unexpected_success_status(
    method: str,
    args: tuple[object, ...],
    status_code: int,
    body: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    agent = await _agent(handler)
    try:
        await _assert_contract_error(agent, method, args, status_code)
    finally:
        await agent.close()


@pytest.mark.parametrize(
    ("method", "args", "status_code", "content", "json_body"),
    [
        ("accept_bid", ("t-1", "bid-1"), 200, b"{", None),
        ("accept_bid", ("t-1", "bid-1"), 200, None, ["not", "an", "object"]),
        ("submit_bid", ("t-1", 75), 201, None, ["not", "an", "object"]),
        ("submit_deliverable", ("t-1",), 200, None, {"task_id": "t-1"}),
        ("approve_task", ("t-1",), 202, None, {"task_id": "t-1"}),
    ],
)
async def test_native_command_rejects_malformed_success_body(
    method: str,
    args: tuple[object, ...],
    status_code: int,
    content: bytes | None,
    json_body: object,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if content is not None:
            return httpx.Response(
                status_code,
                content=content,
                headers={"content-type": "application/json"},
                request=request,
            )
        return httpx.Response(status_code, json=json_body, request=request)

    agent = await _agent(handler)
    try:
        await _assert_contract_error(agent, method, args, status_code)
    finally:
        await agent.close()


@pytest.mark.parametrize(
    ("method", "args", "status_code", "body"),
    [
        ("approve_task", ("t-1",), 200, _pending_body("bank_release")),
        ("approve_task", ("t-1",), 202, _completed_body("bank_release")),
        ("cancel_task", ("t-1",), 202, _pending_body("none")),
    ],
)
async def test_native_command_rejects_status_body_contradiction(
    method: str,
    args: tuple[object, ...],
    status_code: int,
    body: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body, request=request)

    agent = await _agent(handler)
    try:
        await _assert_contract_error(agent, method, args, status_code)
    finally:
        await agent.close()


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("submit_bid", ("t-1", 75)),
        ("accept_bid", ("t-1", "bid-1")),
        ("submit_deliverable", ("t-1",)),
        ("cancel_task", ("t-1",)),
        ("approve_task", ("t-1",)),
        ("dispute_task", ("t-1", "Specification was not met")),
    ],
)
async def test_native_command_preserves_http_error_response(
    method: str,
    args: tuple[object, ...],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json=_ERROR_BODY, request=request)

    agent = await _agent(handler)
    try:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await _call(agent, method, args)
    finally:
        await agent.close()

    assert exc_info.value.response.status_code == 409
    assert exc_info.value.response.json() == _ERROR_BODY


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("submit_bid", ("t-1", 75)),
        ("accept_bid", ("t-1", "bid-1")),
        ("submit_deliverable", ("t-1",)),
        ("cancel_task", ("t-1",)),
        ("approve_task", ("t-1",)),
        ("dispute_task", ("t-1", "Specification was not met")),
    ],
)
async def test_native_command_preserves_transport_timeout(
    method: str,
    args: tuple[object, ...],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("response lost", request=request)

    agent = await _agent(handler)
    try:
        with pytest.raises(httpx.ReadTimeout, match="response lost"):
            await _call(agent, method, args)
    finally:
        await agent.close()

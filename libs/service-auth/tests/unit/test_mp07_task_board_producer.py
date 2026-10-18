"""MP-07 native Task Board fields are explicit shared-SDK inputs."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig
from service_auth.financial_responses import SQLITE_MAX_INTEGER
from service_auth.jws import TokenPolicy, parse_jws

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_OPERATION_ID = "op-00000000-0000-0000-0000-000000000001"
_MAX_DURATION_SECONDS = SQLITE_MAX_INTEGER // 1_000_000


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


async def _agent(captured: list[httpx.Request]) -> BaseAgent:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.path == "/tasks":
            return httpx.Response(200, json={"status": "ok"}, request=request)
        if request.url.path.endswith("/bids"):
            return httpx.Response(
                201,
                json={
                    "bid_id": f"bid-{_OPERATION_ID[3:]}",
                    "task_id": "t-1",
                    "bidder_id": "a-alice",
                    "amount": 75,
                    "submitted_at": "2026-10-18T10:00:00Z",
                },
                request=request,
            )
        suffix = request.url.path.rsplit("/", 1)[-1]
        effect_kind = {
            "accept": "none",
            "submit": "none",
            "cancel": "bank_release",
            "approve": "bank_release",
            "dispute": "court_claim",
        }[suffix]
        return httpx.Response(
            200,
            json={
                "operation": {
                    "operation_state": "completed",
                    "effect_kind": effect_kind,
                    "effect_state": "none" if effect_kind == "none" else "succeeded",
                }
            },
            request=request,
        )

    agent = BaseAgent(_config())
    await agent._http.aclose()
    agent.agent_id = "a-alice"
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return agent


def _token(request: httpx.Request) -> Any:
    return parse_jws(json.loads(request.content)["token"])


@pytest.mark.parametrize(
    ("method", "args", "business", "resource"),
    [
        (
            "cancel_task",
            ("t-1",),
            {"action": "cancel_task", "task_id": "t-1", "poster_id": "a-alice"},
            "POST /tasks/t-1/cancel",
        ),
        (
            "submit_bid",
            ("t-1", 75),
            {"action": "submit_bid", "task_id": "t-1", "bidder_id": "a-alice", "amount": 75},
            "POST /tasks/t-1/bids",
        ),
        (
            "accept_bid",
            ("t-1", "bid-1"),
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
            {"action": "submit_deliverable", "task_id": "t-1", "worker_id": "a-alice"},
            "POST /tasks/t-1/submit",
        ),
        (
            "approve_task",
            ("t-1",),
            {"action": "approve_task", "task_id": "t-1", "poster_id": "a-alice"},
            "POST /tasks/t-1/approve",
        ),
        (
            "dispute_task",
            ("t-1", "Specification was not met"),
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
async def test_mutation_preserves_explicit_identity_across_caller_retry(
    method: str,
    args: tuple[object, ...],
    business: dict[str, object],
    resource: str,
) -> None:
    captured: list[httpx.Request] = []
    agent = await _agent(captured)
    try:
        producer = getattr(agent, method)
        for _ in range(2):
            await producer(
                *args,
                operation_id=_OPERATION_ID,
                expected_lifecycle_version=7,
            )
    finally:
        await agent.close()

    assert len(captured) == 2
    for request in captured:
        token = _token(request)
        assert token.header.aud == "task-board"
        assert token.header.resource == resource
        assert token.payload == {
            **business,
            "operation_id": _OPERATION_ID,
            "expected_lifecycle_version": 7,
        }


async def test_post_task_binds_explicit_window_and_distinct_escrow_token() -> None:
    captured: list[httpx.Request] = []
    agent = await _agent(captured)
    try:
        await agent.post_task(
            "Title",
            "Specification",
            25,
            10,
            20,
            30,
            acceptance_window_seconds=40,
        )
    finally:
        await agent.close()

    assert len(captured) == 1
    body = json.loads(captured[0].content)
    assert body["task_token"] != body["escrow_token"]
    task = parse_jws(body["task_token"])
    escrow = parse_jws(body["escrow_token"])
    assert task.header.aud == "task-board"
    assert task.header.resource == "POST /tasks"
    assert task.payload["acceptance_window_seconds"] == 40
    assert task.payload["execution_deadline_seconds"] == 20
    assert task.payload["review_deadline_seconds"] == 30
    assert escrow.header.aud == "central-bank"
    assert escrow.header.resource == "POST /escrow/lock"
    assert escrow.payload["task_id"] == task.payload["task_id"]
    assert escrow.payload["agent_id"] == task.payload["poster_id"] == "a-alice"
    assert escrow.payload["amount"] == task.payload["reward"] == 25


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("post_task", ("Title", "Spec", 25, 10, 20, 30)),
        ("cancel_task", ("t-1",)),
        ("submit_bid", ("t-1", 75)),
        ("accept_bid", ("t-1", "bid-1")),
        ("submit_deliverable", ("t-1",)),
        ("approve_task", ("t-1",)),
        ("dispute_task", ("t-1", "Reason")),
    ],
)
async def test_native_fields_are_required_keyword_only(
    method: str,
    args: tuple[object, ...],
) -> None:
    captured: list[httpx.Request] = []
    agent = await _agent(captured)
    try:
        with pytest.raises(TypeError):
            await getattr(agent, method)(*args)
    finally:
        await agent.close()
    assert captured == []


@pytest.mark.parametrize("window", [None, True, 0, -1, 1.5, "20", _MAX_DURATION_SECONDS + 1])
async def test_invalid_acceptance_window_is_rejected_before_signing(window: object) -> None:
    captured: list[httpx.Request] = []
    agent = await _agent(captured)
    try:
        with patch.object(agent, "_sign_jws", wraps=agent._sign_jws) as signer:
            with pytest.raises(ValueError, match="acceptance_window_seconds"):
                await agent.post_task(
                    "Title",
                    "Spec",
                    25,
                    10,
                    20,
                    30,
                    acceptance_window_seconds=window,
                )
            signer.assert_not_called()
    finally:
        await agent.close()
    assert captured == []


@pytest.mark.parametrize(
    "operation_id", [None, True, "op-bad", "op-AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"]
)
async def test_invalid_operation_id_is_rejected_before_signing(operation_id: object) -> None:
    captured: list[httpx.Request] = []
    agent = await _agent(captured)
    try:
        with patch.object(agent, "_sign_jws", wraps=agent._sign_jws) as signer:
            with pytest.raises(ValueError, match="operation_id"):
                await agent.cancel_task(
                    "t-1",
                    operation_id=operation_id,
                    expected_lifecycle_version=0,
                )
            signer.assert_not_called()
    finally:
        await agent.close()
    assert captured == []


@pytest.mark.parametrize(
    "version",
    [None, True, -1, 1.5, "0", SQLITE_MAX_INTEGER + 1],
)
async def test_invalid_lifecycle_version_is_rejected_before_signing(version: object) -> None:
    captured: list[httpx.Request] = []
    agent = await _agent(captured)
    try:
        with patch.object(agent, "_sign_jws", wraps=agent._sign_jws) as signer:
            with pytest.raises(ValueError, match="expected_lifecycle_version"):
                await agent.cancel_task(
                    "t-1",
                    operation_id=_OPERATION_ID,
                    expected_lifecycle_version=version,
                )
            signer.assert_not_called()
    finally:
        await agent.close()
    assert captured == []


async def test_sqlite_boundary_values_are_signed_exactly() -> None:
    captured: list[httpx.Request] = []
    agent = await _agent(captured)
    try:
        await agent.post_task(
            "Title",
            "Spec",
            25,
            10,
            20,
            30,
            acceptance_window_seconds=_MAX_DURATION_SECONDS,
        )
        await agent.cancel_task(
            "t-1",
            operation_id=_OPERATION_ID,
            expected_lifecycle_version=SQLITE_MAX_INTEGER,
        )
    finally:
        await agent.close()
    task = parse_jws(json.loads(captured[0].content)["task_token"])
    cancel = _token(captured[1])
    assert task.payload["acceptance_window_seconds"] == _MAX_DURATION_SECONDS
    assert cancel.payload["expected_lifecycle_version"] == SQLITE_MAX_INTEGER

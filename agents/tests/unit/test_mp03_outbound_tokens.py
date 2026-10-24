"""Cryptographic MP-03 coverage for the public agent compatibility package."""

from __future__ import annotations

import json

import httpx
import pytest

from base_agent.agent import BaseAgent
from base_agent.signing import parse_jws
from tests.fakes.mp05_posting_fixtures import posting_response


@pytest.mark.unit
async def test_task_post_emits_separately_bound_strict_tokens(sample_config) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json=posting_response(intent, "published").body)

    agent = BaseAgent(sample_config)
    agent.agent_id = "a-poster"
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        intent = agent.prepare_task_posting(
            "Title",
            "Spec",
            25,
            10,
            acceptance_window_seconds=40,
            execution_deadline_seconds=20,
            review_deadline_seconds=30,
        )
        assert requests == []
        await agent.submit_task_posting(intent)
    finally:
        await agent.close()

    body = json.loads(requests[0].content)
    task = parse_jws(body["task_token"])
    escrow = parse_jws(body["escrow_token"])
    assert vars(task.header) == {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": "a-poster",
        "iat": task.header.iat,
        "exp": task.header.iat + sample_config.token_ttl_seconds,
        "aud": "task-board",
        "resource": "POST /tasks",
    }
    assert task.payload["action"] == "create_task"
    assert task.payload["acceptance_window_seconds"] == 40
    assert task.payload["operation_id"] == intent.operation_id
    assert task.payload["task_id"] == intent.task_id
    assert task.payload == {
        "action": "create_task",
        "protocol_version": 1,
        "operation_id": intent.operation_id,
        "task_id": intent.task_id,
        "poster_id": "a-poster",
        "lock_operation_id": intent.lock_operation_id,
        "title": "Title",
        "spec": "Spec",
        "reward": 25,
        "bidding_deadline_seconds": 10,
        "acceptance_window_seconds": 40,
        "execution_deadline_seconds": 20,
        "review_deadline_seconds": 30,
    }
    assert (escrow.header.aud, escrow.header.resource) == (
        "central-bank",
        "POST /escrow/lock",
    )
    assert escrow.payload["action"] == "escrow_lock"
    assert escrow.payload["operation_id"] == intent.lock_operation_id
    assert escrow.payload == {
        "action": "escrow_lock",
        "operation_id": intent.lock_operation_id,
        "task_id": intent.task_id,
        "amount": 25,
        "agent_id": "a-poster",
    }

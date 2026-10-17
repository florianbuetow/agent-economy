"""Cryptographic MP-03 coverage for the public agent compatibility package."""

from __future__ import annotations

import json

import httpx
import pytest

from base_agent.agent import BaseAgent
from base_agent.signing import parse_jws


@pytest.mark.unit
async def test_task_post_emits_separately_bound_strict_tokens(sample_config) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(201, json={"task_id": "task"})

    agent = BaseAgent(sample_config)
    agent.agent_id = "a-poster"
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await agent.post_task("Title", "Spec", 25, 10, 20, 30)
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
    assert (escrow.header.aud, escrow.header.resource) == (
        "central-bank",
        "POST /escrow/lock",
    )
    assert escrow.payload["action"] == "escrow_lock"

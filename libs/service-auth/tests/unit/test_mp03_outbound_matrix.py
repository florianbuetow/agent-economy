"""Cryptographic route-context coverage for official MP-03 producers."""

from __future__ import annotations

import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth.config import AgentConfig
from service_auth.jws import TokenPolicy, parse_jws
from service_auth.platform import PlatformAgent


def _platform() -> PlatformAgent:
    private_key = Ed25519PrivateKey.generate()
    config = AgentConfig(
        name="Platform",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url="https://identity",
        bank_url="https://bank",
        task_board_url="https://board",
        reputation_url="https://reputation",
        court_url="https://court",
        token_ttl_seconds=60,
        token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=5),
    )
    agent = PlatformAgent(config)
    agent.agent_id = "a-platform"
    return agent


def _assert_token(token: str, audience: str, resource: str, payload: dict[str, object]) -> None:
    parsed = parse_jws(token)
    assert parsed.header.kid == "a-platform"
    assert parsed.header.aud == audience
    assert parsed.header.resource == resource
    assert parsed.header.exp - parsed.header.iat == 60
    assert parsed.payload == payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_platform_bank_court_reputation_and_task_board_tokens() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    agent = _platform()
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    calls = [
        (
            agent.credit_account("acct-1", 9, "ref"),
            "central-bank",
            "POST /accounts/acct-1/credit",
            {"action": "credit", "account_id": "acct-1", "amount": 9, "reference": "ref"},
        ),
        (
            agent.file_claim("task-1", "claimant", "worker", "claim", "esc-1"),
            "court",
            "POST /disputes/file",
            {
                "action": "file_dispute",
                "task_id": "task-1",
                "claimant_id": "claimant",
                "respondent_id": "worker",
                "claim": "claim",
                "escrow_id": "esc-1",
            },
        ),
        (
            agent.submit_feedback("task-1", "worker", "delivery", "good", "ok"),
            "reputation",
            "POST /feedback",
            {
                "action": "submit_feedback",
                "from_agent_id": "a-platform",
                "to_agent_id": "worker",
                "task_id": "task-1",
                "category": "delivery",
                "rating": "good",
                "comment": "ok",
            },
        ),
        (
            agent.record_ruling("task-1", {"action": "record_ruling", "task_id": "task-1"}),
            "task-board",
            "POST /tasks/task-1/ruling",
            {"action": "record_ruling", "task_id": "task-1"},
        ),
    ]
    try:
        for call, audience, resource, payload in calls:
            await call
            body = json.loads(requests[-1].content)
            _assert_token(body["token"], audience, resource, payload)
    finally:
        await agent.close()


@pytest.mark.unit
def test_platform_sign_requires_explicit_context_and_registration() -> None:
    agent = _platform()
    token = agent.sign(
        {"action": "escrow_release"},
        audience="central-bank",
        resource="POST /escrow/esc-1/release",
    )
    _assert_token(
        token,
        "central-bank",
        "POST /escrow/esc-1/release",
        {"action": "escrow_release"},
    )
    agent.agent_id = None
    with pytest.raises(RuntimeError, match="registered"):
        agent.sign(
            {"action": "escrow_release"},
            audience="central-bank",
            resource="POST /escrow/esc-1/release",
        )

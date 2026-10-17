"""Cryptographic route-context coverage for official MP-03 producers."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth.config import AgentConfig
from service_auth.jws import TokenPolicy, parse_jws
from service_auth.platform import PlatformAgent

_CREDIT_OPERATION_ID = "op-00000000-0000-4000-8000-000000000061"


def _credit_response() -> dict[str, object]:
    canonical_request = {
        "request_version": 1,
        "operation_kind": "credit",
        "account_id": "acct-1",
        "amount": 9,
        "reference": "ref",
    }
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": _CREDIT_OPERATION_ID,
            "operation_kind": "credit",
            "request_sha256": hashlib.sha256(
                json.dumps(
                    canonical_request,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
            "source_reference": "ref",
            "committed_at": "2026-10-17T14:00:00Z",
            "event_id": 1,
            "escrow_id": None,
            "result": {
                "account_id": "acct-1",
                "amount": 9,
                "balance_after": 9,
                "reference": "ref",
            },
            "legs": [
                {
                    "role": "account",
                    "account_id": "acct-1",
                    "direction": "credit",
                    "amount": 9,
                    "transaction_id": "tx-credit",
                    "balance_after": 9,
                }
            ],
        },
        "current_escrow": None,
    }


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
        body = _credit_response() if request.url.path.endswith("/credit") else {}
        return httpx.Response(200, json=body)

    agent = _platform()
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    calls = [
        (
            agent.credit_account(
                "acct-1",
                9,
                "ref",
                operation_id=_CREDIT_OPERATION_ID,
            ),
            "central-bank",
            "POST /accounts/acct-1/credit",
            {
                "action": "credit",
                "operation_id": _CREDIT_OPERATION_ID,
                "account_id": "acct-1",
                "amount": 9,
                "reference": "ref",
            },
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

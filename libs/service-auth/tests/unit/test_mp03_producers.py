"""Strict MP-03 producer and configuration acceptance tests."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig, load_agent_config
from service_auth.jws import TokenPolicy, parse_jws
from service_auth.signing import generate_keypair

if TYPE_CHECKING:
    from pathlib import Path


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


@pytest.mark.unit
def test_agent_config_rejects_invalid_ttl_and_policy_mismatch() -> None:
    private_key = Ed25519PrivateKey.generate()
    common = {
        "name": "Alice",
        "private_key": private_key,
        "public_key": private_key.public_key(),
        "identity_url": "https://identity",
        "bank_url": "https://bank",
        "task_board_url": "https://board",
        "reputation_url": "https://reputation",
        "court_url": "https://court",
        "token_policy": TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    }
    for invalid in (None, True, 0, -1):
        with pytest.raises(ValueError):
            AgentConfig(**common, token_ttl_seconds=invalid)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AgentConfig(**common, token_ttl_seconds=301)


@pytest.mark.unit
def test_load_agent_config_requires_complete_strict_signing(tmp_path: Path) -> None:
    keys = tmp_path / "keys"
    keys.mkdir()
    generate_keypair("alice", keys)
    (tmp_path / "roster.yaml").write_text("agents:\n  alice:\n    name: Alice\n")
    base = {
        "platform": {
            "identity_url": "https://identity",
            "bank_url": "https://bank",
            "task_board_url": "https://board",
            "reputation_url": "https://reputation",
            "court_url": "https://court",
        },
        "data": {"keys_dir": "keys", "roster_path": "roster.yaml"},
    }
    for signing in (
        {},
        {"token_ttl_seconds": None, "max_lifetime_seconds": 300, "clock_skew_seconds": 30},
        {"token_ttl_seconds": True, "max_lifetime_seconds": 300, "clock_skew_seconds": 30},
        {"token_ttl_seconds": 301, "max_lifetime_seconds": 300, "clock_skew_seconds": 30},
    ):
        path = tmp_path / "config.yaml"
        path.write_text(json.dumps({**base, "signing": signing}))
        with pytest.raises((ValueError, TypeError)):
            load_agent_config("alice", path)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_post_task_emits_separate_context_bound_tokens() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"task_id": "ok"})

    agent = BaseAgent(_config())
    agent.agent_id = "a-alice"
    await agent._http.aclose()
    agent._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await agent.post_task("Title", "Spec", 25, 10, 20, 30)
    finally:
        await agent.close()

    body = json.loads(captured[0].content)
    task = parse_jws(body["task_token"])
    escrow = parse_jws(body["escrow_token"])
    assert vars(task.header) == {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": "a-alice",
        "iat": task.header.iat,
        "exp": task.header.iat + 300,
        "aud": "task-board",
        "resource": "POST /tasks",
    }
    assert task.payload["action"] == "create_task"
    assert escrow.header.aud == "central-bank"
    assert escrow.header.resource == "POST /escrow/lock"
    assert escrow.payload["action"] == "escrow_lock"


@pytest.mark.unit
def test_unregistered_agent_cannot_sign() -> None:
    agent = BaseAgent(_config())
    with pytest.raises(RuntimeError, match="registered"):
        agent._sign_jws(
            {"action": "get_balance"},
            audience="central-bank",
            resource="GET /accounts/a-alice",
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "policy",
    [None, object(), SimpleNamespace(max_lifetime_seconds=300, clock_skew_seconds="invalid")],
)
def test_agent_config_requires_validated_token_policy(policy: object) -> None:
    with pytest.raises(ValueError, match="token_policy must be a TokenPolicy"):
        replace(_config(), token_policy=policy)

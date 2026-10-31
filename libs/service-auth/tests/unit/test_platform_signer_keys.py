"""PlatformAgent signs with its registered in-memory identity key."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from service_auth import jws
from service_auth.config import AgentConfig
from service_auth.jws import (
    ForbiddenError,
    InvalidJwsError,
    TokenExpiredError,
    TokenNotYetValidError,
    TokenPolicy,
)
from service_auth.platform import PlatformAgent
from service_auth.signing import verify_jws


def _platform_agent() -> PlatformAgent:
    private_key = Ed25519PrivateKey.generate()
    return PlatformAgent(
        AgentConfig(
            name="Platform",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="http://identity.test",
            bank_url="http://bank.test",
            task_board_url="http://task-board.test",
            reputation_url="http://reputation.test",
            court_url="http://court.test",
            token_ttl_seconds=60,
            token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
        )
    )


def _protected_header(token: str) -> dict[str, object]:
    protected_segment = token.split(".", maxsplit=1)[0]
    padding = "=" * (-len(protected_segment) % 4)
    return json.loads(base64.urlsafe_b64decode(protected_segment + padding))


@pytest.mark.unit
async def test_platform_agent_refuses_to_sign_before_registration() -> None:
    agent = _platform_agent()
    try:
        with pytest.raises(RuntimeError, match="registered"):
            agent.sign(
                {"action": "escrow_release"},
                audience="central-bank",
                resource="POST /escrow/esc-1/release",
            )
    finally:
        await agent.close()


@pytest.mark.unit
async def test_platform_agent_signs_with_registered_identity_and_config_key() -> None:
    agent = _platform_agent()
    agent.agent_id = "a-platform-registered"
    payload = {"action": "escrow_release", "escrow_id": "esc-1"}
    try:
        token = agent.sign(payload, audience="central-bank", resource="POST /escrow/esc-1/release")

        assert (
            verify_jws(token, agent.config.public_key, policy=agent.config.token_policy).payload
            == payload
        )
        assert _protected_header(token)["kid"] == "a-platform-registered"
    finally:
        await agent.close()


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{}, {"action": True}])
async def test_local_principal_rejection_precedes_action_content(
    payload: dict[str, object],
) -> None:
    agent = _platform_agent()
    agent.agent_id = "a-platform-registered"
    try:
        valid = agent.sign(
            {"action": "escrow_release"},
            audience="central-bank",
            resource="POST /escrow/esc-1/release",
        )
        header = _protected_header(valid)
        header["kid"] = "a-different-principal"
        first = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
        second = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        signature = agent.config.private_key.sign(f"{first}.{second}".encode())
        signature_segment = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        token = f"{first}.{second}.{signature_segment}"
        with pytest.raises(ForbiddenError):
            agent.validate_certificate(token)
    finally:
        await agent.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    "case,error",
    [
        ("expired", TokenExpiredError),
        ("future", TokenNotYetValidError),
        ("overlong", InvalidJwsError),
        ("bad_signature", InvalidSignature),
    ],
)
async def test_crypto_and_policy_precede_local_principal(
    case: str, error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    anchor = datetime(2030, 1, 1, tzinfo=UTC)
    now = int(anchor.timestamp())
    monkeypatch.setattr(jws, "_clock", lambda: anchor)
    agent = _platform_agent()
    agent.agent_id = "a-platform-registered"
    header = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": "a-different-principal",
        "iat": now,
        "exp": now + 60,
        "aud": "central-bank",
        "resource": "POST /escrow/esc-1/release",
    }
    if case == "expired":
        header.update(iat=now - 100, exp=now - 30)
    elif case == "future":
        header.update(iat=now + 31, exp=now + 91)
    elif case == "overlong":
        header["exp"] = now + 301
    first = base64.urlsafe_b64encode(json.dumps(header).encode()).rstrip(b"=").decode()
    second = base64.urlsafe_b64encode(b"{}").rstrip(b"=").decode()
    signature = (
        bytes(64)
        if case == "bad_signature"
        else agent.config.private_key.sign(f"{first}.{second}".encode())
    )
    token = f"{first}.{second}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
    try:
        with pytest.raises(error):
            agent.validate_certificate(token)
    finally:
        await agent.close()

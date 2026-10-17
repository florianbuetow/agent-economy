"""MP-03 receiver-context admission tests for Central Bank."""

from __future__ import annotations

import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.jws import JwsProtectedHeader, TokenPolicy, VerifiedJws, create_jws
from service_auth.platform import PlatformAgent
from service_commons.exceptions import ServiceError
from starlette.requests import Request

from central_bank_service.core.state import init_app_state, reset_app_state
from central_bank_service.routers.accounts import credit_account
from central_bank_service.routers.helpers import verify_jws_token, verify_platform_signature


def _verified(*, audience: str, resource: str, action: str) -> VerifiedJws:
    return VerifiedJws(
        header=JwsProtectedHeader(
            alg="EdDSA",
            typ="JWT",
            kid="a-agent",
            iat=1,
            exp=2,
            aud=audience,
            resource=resource,
        ),
        payload={"action": action},
    )


class _Identity:
    async def verify_jws(self, _token: str) -> VerifiedJws:
        return _verified(audience="task-board", resource="POST /accounts", action="create_account")


class _InvalidSignatureIdentity:
    async def verify_jws(self, _token: str) -> VerifiedJws:
        raise InvalidSignature


class _Ledger:
    def __init__(self) -> None:
        self.credit_called = False

    async def credit(self, _account_id: str, _amount: int, _reference: str) -> dict[str, object]:
        self.credit_called = True
        return {}


def _request(body: dict[str, object]) -> Request:
    encoded = json.dumps(body).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": encoded, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/accounts/a-1/credit", "headers": []},
        receive,
    )


@pytest.mark.unit
async def test_remote_verifier_rejects_wrong_audience_before_caller_use() -> None:
    state = init_app_state()
    state.identity_client = _Identity()  # type: ignore[assignment]
    try:
        with pytest.raises(ServiceError) as exc_info:
            await verify_jws_token(
                "token",
                audience="central-bank",
                resource="POST /accounts",
                allowed_action="create_account",
            )
        assert exc_info.value.error == "invalid_payload"
    finally:
        reset_app_state()


@pytest.mark.unit
async def test_remote_invalid_signature_maps_to_forbidden() -> None:
    state = init_app_state()
    state.identity_client = _InvalidSignatureIdentity()  # type: ignore[assignment]
    try:
        with pytest.raises(ServiceError) as exc_info:
            await verify_jws_token(
                "token",
                audience="central-bank",
                resource="POST /accounts",
                allowed_action="create_account",
            )
        assert exc_info.value.error == "forbidden"
        assert exc_info.value.status_code == 403
    finally:
        reset_app_state()


@pytest.mark.unit
async def test_local_adapter_cryptographically_rejects_unregistered_kid() -> None:
    private_key = Ed25519PrivateKey.generate()
    agent = PlatformAgent(
        AgentConfig(
            name="Platform",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="http://127.0.0.1:0",
            bank_url="http://127.0.0.1:0",
            task_board_url="http://127.0.0.1:0",
            reputation_url="http://127.0.0.1:0",
            court_url="http://127.0.0.1:0",
            token_ttl_seconds=60,
            token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
        )
    )
    agent.agent_id = "a-platform"
    state = init_app_state()
    state.platform_agent = agent
    token = create_jws(
        {"action": "credit"},
        private_key,
        kid="a-rogue",
        audience="central-bank",
        resource="POST /accounts/a-1/credit",
        token_ttl_seconds=60,
    )
    try:
        with pytest.raises(ServiceError) as exc_info:
            verify_platform_signature(
                token,
                audience="central-bank",
                resource="POST /accounts/a-1/credit",
                allowed_action="credit",
            )
        assert exc_info.value.error == "forbidden"
    finally:
        await agent.close()
        reset_app_state()


@pytest.mark.unit
async def test_wrong_authenticated_context_has_no_ledger_effect() -> None:
    private_key = Ed25519PrivateKey.generate()
    agent = PlatformAgent(
        AgentConfig(
            name="Platform",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="http://127.0.0.1:0",
            bank_url="http://127.0.0.1:0",
            task_board_url="http://127.0.0.1:0",
            reputation_url="http://127.0.0.1:0",
            court_url="http://127.0.0.1:0",
            token_ttl_seconds=60,
            token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
        )
    )
    agent.agent_id = "a-platform"
    ledger = _Ledger()
    state = init_app_state()
    state.platform_agent = agent
    state.ledger = ledger  # type: ignore[assignment]
    token = create_jws(
        {"action": "credit", "account_id": "a-1", "amount": 5, "reference": "r-1"},
        private_key,
        kid="a-platform",
        audience="task-board",
        resource="POST /accounts/a-1/credit",
        token_ttl_seconds=60,
    )
    try:
        with pytest.raises(ServiceError) as exc_info:
            await credit_account(_request({"token": token}), "a-1")
        assert exc_info.value.error == "invalid_payload"
        assert ledger.credit_called is False
    finally:
        await agent.close()
        reset_app_state()

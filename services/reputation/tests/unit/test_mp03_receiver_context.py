"""MP-03 receiver-context admission tests for Reputation."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.jws import (
    JwsAdmissionError,
    JwsProtectedHeader,
    TokenPolicy,
    VerifiedJws,
    create_jws,
    verify_jws,
)
from service_auth.platform import PlatformAgent
from service_clients.identity import IdentityClient
from starlette.requests import Request

from reputation_service.config import IdentityConfig, Settings
from reputation_service.core.exceptions import ServiceError
from reputation_service.core.state import init_app_state, reset_app_state
from reputation_service.routers.feedback import submit_feedback_endpoint
from reputation_service.services.platform_identity_client import PlatformJwsVerifier


class _Platform:
    agent_id = "a-platform"

    def validate_certificate(self, _token: str) -> VerifiedJws:
        return VerifiedJws(
            header=JwsProtectedHeader(
                alg="EdDSA",
                typ="JWT",
                kid=self.agent_id,
                iat=1,
                exp=2,
                aud="reputation",
                resource="POST /feedback",
            ),
            payload={"action": "submit_feedback"},
        )


class _BrokenPlatform:
    def validate_certificate(self, _token: str) -> VerifiedJws:
        raise RuntimeError("unexpected local verifier failure")


class _RemoteFailure:
    async def verify_jws(self, _token: str) -> VerifiedJws:
        raise ServiceError(
            "identity_service_unavailable",
            "Identity verification failed",
            502,
            {},
        )

    async def close(self) -> None: ...


class _RemoteInvalidSignature:
    async def verify_jws(self, _token: str) -> VerifiedJws:
        raise InvalidSignature

    async def close(self) -> None: ...


class _Store:
    called = False


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _agent(private_key: Ed25519PrivateKey, agent_id: str) -> PlatformAgent:
    agent = PlatformAgent(
        AgentConfig(
            name="Verifier",
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url="http://identity.test",
            bank_url="http://127.0.0.1:0",
            task_board_url="http://127.0.0.1:0",
            reputation_url="http://127.0.0.1:0",
            court_url="http://127.0.0.1:0",
            token_ttl_seconds=60,
            token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
        )
    )
    agent.agent_id = agent_id
    return agent


def _raw_token(
    private_key: Ed25519PrivateKey,
    kid: str,
    payload: dict[str, object],
    *,
    tampered: bool,
) -> str:
    issued_at = int(datetime.now(UTC).timestamp())
    header = {
        "alg": "EdDSA",
        "typ": "JWT",
        "kid": kid,
        "iat": issued_at,
        "exp": issued_at + 60,
        "aud": "reputation",
        "resource": "POST /feedback",
    }
    header_segment = _b64url_encode(
        json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
    )
    payload_segment = _b64url_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    )
    signing_input = f"{header_segment}.{payload_segment}".encode()
    signature = bytearray(private_key.sign(signing_input))
    if tampered:
        signature[0] ^= 1
    return f"{header_segment}.{payload_segment}.{_b64url_encode(bytes(signature))}"


async def _identity_client(public_key: object) -> IdentityClient:
    policy = TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0)

    def handler(request: httpx.Request) -> httpx.Response:
        token = json.loads(request.content)["token"]
        try:
            verified = verify_jws(token, public_key, policy=policy)  # type: ignore[arg-type]
        except InvalidSignature:
            return httpx.Response(200, json={"valid": False, "reason": "signature mismatch"})
        except JwsAdmissionError as exc:
            return httpx.Response(
                exc.status_code,
                json={"error": exc.error_code, "message": str(exc)},
            )
        return httpx.Response(
            200,
            json={
                "valid": True,
                "agent_id": verified.header.kid,
                "protected_header": asdict(verified.header),
                "payload": verified.payload,
            },
        )

    client = IdentityClient(
        base_url="http://identity.test",
        get_agent_path="/agents",
        verify_jws_path="/agents/verify-jws",
        timeout_seconds=1,
    )
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://identity.test",
    )
    return client


def _request(token: str) -> Request:
    encoded = json.dumps({"token": token}).encode()

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": encoded, "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/feedback", "headers": []},
        receive,
    )


def _deep_header_token() -> str:
    header = b'{"nested":' + (b"[" * 2000) + b"0" + (b"]" * 2000) + b"}"
    payload = b'{"action":"submit_feedback"}'
    signature = b"x" * 64
    return f"{_b64url_encode(header)}.{_b64url_encode(payload)}.{_b64url_encode(signature)}"


@pytest.mark.unit
def test_identity_configuration_has_no_optional_fallback() -> None:
    assert Settings.model_fields["identity"].is_required()
    assert IdentityConfig.model_fields["get_agent_path"].is_required()
    assert IdentityConfig.model_fields["timeout_seconds"].is_required()


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        {
            "base_url": "",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "/agents",
            "verify_jws_path": "verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 0,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": True,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": "1",
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1.0,
        },
        {
            "base_url": " http://identity",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://:",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://identity#fragment",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": " /agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify?query=1",
            "timeout_seconds": 1,
        },
    ],
)
def test_identity_configuration_rejects_invalid_values(value: dict[str, object]) -> None:
    with pytest.raises(Exception):  # noqa: B017
        IdentityConfig(**value)  # type: ignore[arg-type]


@pytest.mark.unit
async def test_local_platform_adapter_returns_verified_jws() -> None:
    platform = _Platform()
    verifier = PlatformJwsVerifier(lambda: platform)  # type: ignore[arg-type]
    verified = await verifier.verify_jws("token")
    assert isinstance(verified, VerifiedJws)
    assert verified.header.kid == "a-platform"


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
    token = create_jws(
        {"action": "submit_feedback"},
        private_key,
        kid="a-rogue",
        audience="reputation",
        resource="POST /feedback",
        token_ttl_seconds=60,
    )
    try:
        verifier = PlatformJwsVerifier(lambda: agent)
        with pytest.raises(ServiceError) as exc_info:
            await verifier.verify_jws(token)
        assert exc_info.value.error == "forbidden"
    finally:
        await agent.close()


@pytest.mark.unit
async def test_unexpected_local_verifier_error_is_not_mapped_to_remote_502() -> None:
    broken = _BrokenPlatform()
    verifier = PlatformJwsVerifier(lambda: broken)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="unexpected local verifier failure"):
        await verifier.verify_jws("token")


@pytest.mark.unit
async def test_true_remote_502_has_no_feedback_effect() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {
            "action": "submit_feedback",
            "task_id": "t-1",
            "from_agent_id": "a-agent",
            "to_agent_id": "a-other",
            "category": "delivery_quality",
            "rating": "satisfied",
        },
        private_key,
        kid="a-agent",
        audience="reputation",
        resource="POST /feedback",
        token_ttl_seconds=60,
    )
    state = init_app_state()
    store = _Store()
    state.feedback_store = store  # type: ignore[assignment]
    state.identity_client = _RemoteFailure()
    try:
        with pytest.raises(ServiceError) as exc_info:
            await submit_feedback_endpoint(_request(token))
        assert exc_info.value.status_code == 502
        assert exc_info.value.error == "identity_service_unavailable"
        assert store.called is False
    finally:
        reset_app_state()


@pytest.mark.unit
async def test_remote_invalid_signature_maps_to_forbidden_without_effect() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = create_jws(
        {"action": "submit_feedback"},
        private_key,
        kid="a-agent",
        audience="reputation",
        resource="POST /feedback",
        token_ttl_seconds=60,
    )
    state = init_app_state()
    store = _Store()
    state.feedback_store = store  # type: ignore[assignment]
    state.identity_client = _RemoteInvalidSignature()
    try:
        with pytest.raises(ServiceError) as exc_info:
            await submit_feedback_endpoint(_request(token))
        assert exc_info.value.status_code == 403
        assert exc_info.value.error == "forbidden"
        assert store.called is False
    finally:
        reset_app_state()


@pytest.mark.unit
async def test_deep_header_is_invalid_jws_without_feedback_effect() -> None:
    state = init_app_state()
    store = _Store()
    state.feedback_store = store  # type: ignore[assignment]
    state.identity_client = _RemoteFailure()
    try:
        with pytest.raises(ServiceError) as exc_info:
            await submit_feedback_endpoint(_request(_deep_header_token()))
        assert exc_info.value.status_code == 400
        assert exc_info.value.error == "invalid_jws"
        assert store.called is False
    finally:
        reset_app_state()


@pytest.mark.unit
@pytest.mark.parametrize("tier", ["local", "remote"])
@pytest.mark.parametrize("payload", [{}, {"action": 7}])
async def test_bad_signature_precedes_malformed_action_without_effect(
    tier: str,
    payload: dict[str, object],
) -> None:
    private_key = Ed25519PrivateKey.generate()
    agent_id = "a-platform" if tier == "local" else "a-agent"
    token = _raw_token(private_key, agent_id, payload, tampered=True)
    store = _Store()
    state = init_app_state()
    state.feedback_store = store  # type: ignore[assignment]
    local_agent: PlatformAgent | None = None
    identity_client: IdentityClient | None = None
    if tier == "local":
        local_agent = _agent(private_key, agent_id)
        state.platform_agent = local_agent
        state.platform_verifier = PlatformJwsVerifier(lambda: local_agent)
    else:
        identity_client = await _identity_client(private_key.public_key())
        state.identity_client = identity_client
    try:
        with pytest.raises(ServiceError) as exc_info:
            await submit_feedback_endpoint(_request(token))
        assert exc_info.value.status_code == 403
        assert exc_info.value.error == "forbidden"
        assert store.called is False
    finally:
        if identity_client is not None:
            await identity_client.close()
        if local_agent is not None:
            await local_agent.close()
        reset_app_state()


@pytest.mark.unit
@pytest.mark.parametrize("tier", ["local", "remote"])
async def test_authentic_wrong_action_is_invalid_payload_without_effect(tier: str) -> None:
    private_key = Ed25519PrivateKey.generate()
    agent_id = "a-platform" if tier == "local" else "a-agent"
    token = _raw_token(private_key, agent_id, {"action": "credit"}, tampered=False)
    store = _Store()
    state = init_app_state()
    state.feedback_store = store  # type: ignore[assignment]
    local_agent: PlatformAgent | None = None
    identity_client: IdentityClient | None = None
    if tier == "local":
        local_agent = _agent(private_key, agent_id)
        state.platform_agent = local_agent
        state.platform_verifier = PlatformJwsVerifier(lambda: local_agent)
    else:
        identity_client = await _identity_client(private_key.public_key())
        state.identity_client = identity_client
    try:
        with pytest.raises(ServiceError) as exc_info:
            await submit_feedback_endpoint(_request(token))
        assert exc_info.value.status_code == 400
        assert exc_info.value.error == "invalid_payload"
        assert store.called is False
    finally:
        if identity_client is not None:
            await identity_client.close()
        if local_agent is not None:
            await local_agent.close()
        reset_app_state()


@pytest.mark.unit
async def test_authenticated_from_agent_mismatch_remains_domain_forbidden() -> None:
    private_key = Ed25519PrivateKey.generate()
    token = _raw_token(
        private_key,
        "a-agent",
        {
            "action": "submit_feedback",
            "task_id": "t-1",
            "from_agent_id": "a-other",
            "to_agent_id": "a-recipient",
            "category": "delivery_quality",
            "rating": "satisfied",
        },
        tampered=False,
    )
    identity_client = await _identity_client(private_key.public_key())
    store = _Store()
    state = init_app_state()
    state.feedback_store = store  # type: ignore[assignment]
    state.identity_client = identity_client
    try:
        with pytest.raises(ServiceError) as exc_info:
            await submit_feedback_endpoint(_request(token))
        assert exc_info.value.status_code == 403
        assert exc_info.value.error == "forbidden"
        assert store.called is False
    finally:
        await identity_client.close()
        reset_app_state()

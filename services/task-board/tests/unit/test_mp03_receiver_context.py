"""MP-03 receiver-context admission tests for Task Board."""

from __future__ import annotations

import base64

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError
from service_auth.config import AgentConfig
from service_auth.jws import JwsProtectedHeader, TokenPolicy, VerifiedJws, create_jws
from service_auth.platform import PlatformAgent
from service_commons.exceptions import ServiceError

from task_board_service.config import IdentityConfig, Settings
from task_board_service.services.token_validator import TokenValidator


class _Identity:
    async def verify_jws(self, _token: str) -> VerifiedJws:
        return VerifiedJws(
            header=JwsProtectedHeader(
                alg="EdDSA",
                typ="JWT",
                kid="a-agent",
                iat=1,
                exp=2,
                aud="task-board",
                resource="POST /tasks/wrong/submit",
            ),
            payload={"action": "submit_deliverable"},
        )


class _InvalidSignatureIdentity:
    async def verify_jws(self, _token: str) -> VerifiedJws:
        raise InvalidSignature


class _PlatformFallback:
    def validate_certificate(self, _token: str) -> VerifiedJws:
        return VerifiedJws(
            header=JwsProtectedHeader(
                alg="EdDSA",
                typ="JWT",
                kid="a-platform",
                iat=1,
                exp=2,
                aud="task-board",
                resource="POST /tasks",
            ),
            payload={"action": "create_task"},
        )


@pytest.mark.unit
def test_identity_configuration_is_required_and_strict() -> None:
    assert Settings.model_fields["identity"].is_required()
    invalid_values = [
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
            "base_url": "http://identity?query=1",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "/agents?query=1",
            "verify_jws_path": "/verify",
            "timeout_seconds": 1,
        },
        {
            "base_url": "http://identity",
            "get_agent_path": "/agents",
            "verify_jws_path": "/verify#fragment",
            "timeout_seconds": 1,
        },
    ]
    for value in invalid_values:
        with pytest.raises(ValidationError):
            IdentityConfig(**value)


@pytest.mark.unit
async def test_validator_rejects_wrong_resource() -> None:
    validator = TokenValidator(platform_agent=object(), identity_client=_Identity())  # type: ignore[arg-type]
    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(
            "a.b.c",
            "submit_deliverable",
            expected_resource="POST /tasks/t-1/submit",
        )
    assert exc_info.value.error == "invalid_payload"


@pytest.mark.unit
async def test_remote_invalid_signature_maps_to_forbidden() -> None:
    validator = TokenValidator(
        platform_agent=_PlatformFallback(),  # type: ignore[arg-type]
        identity_client=_InvalidSignatureIdentity(),  # type: ignore[arg-type]
    )
    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(
            "a.b.c",
            "create_task",
            expected_resource="POST /tasks",
        )
    assert exc_info.value.error == "forbidden"
    assert exc_info.value.status_code == 403


@pytest.mark.unit
async def test_ordinary_agent_validation_has_no_local_fallback() -> None:
    validator = TokenValidator(
        platform_agent=_PlatformFallback(),  # type: ignore[arg-type]
        identity_client=None,
    )
    with pytest.raises(ServiceError) as exc_info:
        await validator.validate_jws_token(
            "a.b.c",
            "create_task",
            expected_resource="POST /tasks",
        )
    assert exc_info.value.error == "service_not_ready"


@pytest.mark.unit
def test_validator_requires_identity_dependency_argument() -> None:
    with pytest.raises(TypeError):
        TokenValidator(platform_agent=_PlatformFallback())  # type: ignore[call-arg,arg-type]


@pytest.mark.unit
def test_deep_escrow_payload_maps_to_invalid_jws() -> None:
    header = b'{"alg":"EdDSA"}'
    payload = b'{"nested":' + (b"[" * 2000) + b"0" + (b"]" * 2000) + b"}"
    signature = b"x" * 64

    def encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

    token = f"{encode(header)}.{encode(payload)}.{encode(signature)}"
    validator = TokenValidator(
        platform_agent=_PlatformFallback(),  # type: ignore[arg-type]
        identity_client=None,
    )
    with pytest.raises(ServiceError) as exc_info:
        validator.decode_escrow_token_payload(token)
    assert exc_info.value.error == "invalid_jws"
    assert exc_info.value.status_code == 400


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
        {"action": "record_ruling"},
        private_key,
        kid="a-rogue",
        audience="task-board",
        resource="POST /tasks/t-1/ruling",
        token_ttl_seconds=60,
    )
    try:
        validator = TokenValidator(platform_agent=agent, identity_client=None)
        with pytest.raises(ServiceError) as exc_info:
            await validator.validate_platform_jws_token(
                token,
                "record_ruling",
                expected_resource="POST /tasks/t-1/ruling",
            )
        assert exc_info.value.error == "forbidden"
    finally:
        await agent.close()

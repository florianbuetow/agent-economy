"""MP-03 receiver-context admission tests for Court."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.config import AgentConfig
from service_auth.jws import JwsProtectedHeader, TokenPolicy, VerifiedJws, create_jws
from service_auth.platform import PlatformAgent
from service_commons.exceptions import ServiceError

from court_service.routers.validation import verify_platform_token


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
                aud="court",
                resource="POST /disputes/wrong/rule",
            ),
            payload={"action": "trigger_ruling"},
        )


@pytest.mark.unit
def test_local_verifier_rejects_wrong_resource() -> None:
    with pytest.raises(ServiceError) as exc_info:
        verify_platform_token(
            "token",
            _Platform(),  # type: ignore[arg-type]
            audience="court",
            resource="POST /disputes/d-1/rule",
            allowed_action="trigger_ruling",
        )
    assert exc_info.value.error == "invalid_payload"


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
        {"action": "file_dispute"},
        private_key,
        kid="a-rogue",
        audience="court",
        resource="POST /disputes/file",
        token_ttl_seconds=60,
    )
    try:
        with pytest.raises(ServiceError) as exc_info:
            verify_platform_token(
                token,
                agent,
                audience="court",
                resource="POST /disputes/file",
                allowed_action="file_dispute",
            )
        assert exc_info.value.error == "forbidden"
    finally:
        await agent.close()

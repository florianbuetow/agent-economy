"""Database Gateway capability signing contract tests."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from service_auth.gateway import (
    GatewayRequestSigner,
    GatewaySignerConfig,
    GatewayTrustConfig,
    GatewayTrustedCallerConfig,
    verify_gateway_request,
)
from service_auth.jws import InvalidPayloadError, TokenPolicy
from service_auth.signing import generate_keypair, load_public_key


@pytest.fixture
def gateway_signer(tmp_path: Path) -> GatewayRequestSigner:
    """Create one canonical service capability signer."""
    generate_keypair("gateway-identity", tmp_path)
    return GatewayRequestSigner(
        GatewaySignerConfig(
            kid="gateway-identity",
            private_key_path=Path("gateway-identity.key"),
            audience="db-gateway",
            token_ttl_seconds=30,
        ),
        config_dir=tmp_path,
    )


@pytest.mark.unit
def test_signs_final_httpx_body_query_and_encoded_path(
    gateway_signer: GatewayRequestSigner,
    tmp_path: Path,
) -> None:
    request = httpx.Request(
        "POST",
        "http://gateway/identity/agents/a%2Fb?name=Alice%20Smith",
        content=b'{"name":"Alice"}',
    )

    signed = gateway_signer.sign_request(request, action="gateway.identity.write")
    token = signed.headers["Authorization"].removeprefix("Bearer ")
    verified = verify_gateway_request(
        token,
        load_public_key(tmp_path / "gateway-identity.pub"),
        policy=TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0),
        audience="db-gateway",
        method="POST",
        path="/identity/agents/a%2Fb",
        body=b'{"name":"Alice"}',
        query=b"name=Alice%20Smith",
        allowed_actions="gateway.identity.write",
    )

    assert verified.payload["action"] == "gateway.identity.write"
    assert signed.headers.get_list("Authorization") == [signed.headers["Authorization"]]


@pytest.mark.unit
def test_rejects_tampered_body_or_query(
    gateway_signer: GatewayRequestSigner,
    tmp_path: Path,
) -> None:
    request = gateway_signer.sign_request(
        httpx.Request("GET", "http://gateway/identity/agents?limit=1", content=b""),
        action="gateway.identity.read",
    )
    token = request.headers["Authorization"].removeprefix("Bearer ")
    public_key = load_public_key(tmp_path / "gateway-identity.pub")
    policy = TokenPolicy(max_lifetime_seconds=30, clock_skew_seconds=0)

    with pytest.raises(InvalidPayloadError):
        verify_gateway_request(
            token,
            public_key,
            policy=policy,
            audience="db-gateway",
            method="GET",
            path="/identity/agents",
            body=b"tampered",
            query=b"limit=1",
            allowed_actions="gateway.identity.read",
        )
    with pytest.raises(InvalidPayloadError):
        verify_gateway_request(
            token,
            public_key,
            policy=policy,
            audience="db-gateway",
            method="GET",
            path="/identity/agents",
            body=b"",
            query=b"limit=2",
            allowed_actions="gateway.identity.read",
        )


@pytest.mark.unit
def test_rejects_existing_authorization_and_unread_stream(
    gateway_signer: GatewayRequestSigner,
) -> None:
    existing = httpx.Request(
        "GET",
        "http://gateway/identity/agents",
        headers={"Authorization": "Bearer existing"},
    )
    with pytest.raises(ValueError, match="already contains"):
        gateway_signer.sign_request(existing, action="gateway.identity.read")

    streaming = httpx.Request(
        "POST",
        "http://gateway/identity/agents",
        content=iter([b"one", b"two"]),
    )
    with pytest.raises(ValueError, match="must be read"):
        gateway_signer.sign_request(streaming, action="gateway.identity.write")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [("kid", " "), ("audience", ""), ("token_ttl_seconds", 0)],
)
def test_config_rejects_invalid_required_values(field: str, value: object) -> None:
    values: dict[str, object] = {
        "kid": "gateway-identity",
        "private_key_path": Path("gateway-identity.key"),
        "audience": "db-gateway",
        "token_ttl_seconds": 30,
    }
    values[field] = value
    with pytest.raises(ValidationError):
        GatewaySignerConfig(**values)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("invalid_ttl", [True, "30", 30.0])
def test_signer_config_rejects_coercible_ttl_types(invalid_ttl: object) -> None:
    with pytest.raises(ValidationError):
        GatewaySignerConfig(
            kid="gateway-identity",
            private_key_path=Path("gateway-identity.key"),
            audience="db-gateway",
            token_ttl_seconds=invalid_ttl,  # type: ignore[arg-type]
        )


@pytest.mark.unit
def test_signer_config_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        GatewaySignerConfig(
            kid="gateway-identity",
            private_key_path="gateway-identity.key",
            audience="db-gateway",
            token_ttl_seconds=30,
            extra=True,  # type: ignore[call-arg]
        )


@pytest.mark.unit
def test_trust_config_accepts_yaml_paths_without_scalar_coercion() -> None:
    config = GatewayTrustConfig(
        audience="db-gateway",
        max_lifetime_seconds=30,
        clock_skew_seconds=0,
        trusted_callers={
            "gateway-identity": GatewayTrustedCallerConfig(
                public_key_path="keys/gateway-identity.pub",
                capabilities=["gateway.identity.read"],
            )
        },
    )

    assert config.trusted_callers["gateway-identity"].public_key_path == Path(
        "keys/gateway-identity.pub"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("max_lifetime_seconds", True),
        ("max_lifetime_seconds", "30"),
        ("max_lifetime_seconds", 30.0),
        ("clock_skew_seconds", False),
        ("clock_skew_seconds", "0"),
        ("clock_skew_seconds", 0.0),
    ],
)
def test_trust_config_rejects_coercible_policy_types(field: str, invalid_value: object) -> None:
    values: dict[str, object] = {
        "audience": "db-gateway",
        "max_lifetime_seconds": 30,
        "clock_skew_seconds": 0,
        "trusted_callers": {
            "gateway-identity": {
                "public_key_path": "gateway-identity.pub",
                "capabilities": ["gateway.identity.read"],
            }
        },
    }
    values[field] = invalid_value

    with pytest.raises(ValidationError):
        GatewayTrustConfig(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_trust_config_rejects_non_path_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        GatewayTrustConfig(
            audience="db-gateway",
            max_lifetime_seconds=30,
            clock_skew_seconds=0,
            trusted_callers={
                "gateway-identity": {
                    "public_key_path": True,
                    "capabilities": ["gateway.identity.read"],
                }
            },
            extra=True,  # type: ignore[call-arg]
        )

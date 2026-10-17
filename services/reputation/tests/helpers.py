"""Shared test helpers for JWS authentication."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.jws import VerifiedJws, create_jws, parse_jws

_PRIVATE_KEYS: dict[str, Ed25519PrivateKey] = {}


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def make_jws_token(payload: dict[str, Any], kid: str = "a-test-agent") -> str:
    """Build a strict test token for the real Reputation route."""
    private_key = _PRIVATE_KEYS.setdefault(kid, Ed25519PrivateKey.generate())
    return create_jws(
        payload,
        private_key,
        kid=kid,
        audience="reputation",
        resource="POST /feedback",
        token_ttl_seconds=60,
    )


def make_profile_jws_with_unchecked_payload(
    payload: dict[str, Any],
    kid: str = "a-test-agent",
) -> str:
    """Sign a malformed domain payload under an otherwise strict protected header."""
    private_key = _PRIVATE_KEYS.setdefault(kid, Ed25519PrivateKey.generate())
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
    return f"{header_segment}.{payload_segment}.{_b64url_encode(private_key.sign(signing_input))}"


def _verify_test_token(token: str, payload_override: dict[str, Any] | None) -> VerifiedJws:
    parsed = parse_jws(token)
    private_key = _PRIVATE_KEYS.get(parsed.header.kid)
    if private_key is None:
        raise InvalidSignature
    private_key.public_key().verify(parsed.signature, parsed.signing_input)
    return VerifiedJws(
        header=parsed.header,
        payload=payload_override if payload_override is not None else parsed.payload,
    )


def make_mock_platform_agent(
    verify_payload: dict[str, Any] | None = None,
    verify_side_effect: Exception | None = None,
) -> MagicMock:
    """Create a mock PlatformAgent used by router tests."""
    mock_agent = MagicMock()
    mock_agent.close = AsyncMock()

    if verify_side_effect is not None:
        mock_agent.validate_certificate.side_effect = verify_side_effect
    else:
        mock_agent.validate_certificate.side_effect = lambda token: _verify_test_token(
            token,
            verify_payload,
        )

    return mock_agent

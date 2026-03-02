"""Shared test helpers for JWS authentication."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from cryptography.exceptions import InvalidSignature
from service_commons.exceptions import ServiceError


def make_jws_token(payload: dict[str, Any], kid: str = "a-test-agent") -> str:
    """Build a fake but structurally valid JWS compact serialization."""
    header = (
        base64.urlsafe_b64encode(json.dumps({"alg": "EdDSA", "kid": kid}).encode())
        .rstrip(b"=")
        .decode()
    )
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    signature = base64.urlsafe_b64encode(b"fake-signature").rstrip(b"=").decode()
    return f"{header}.{body}.{signature}"


def _decode_jws_payload(token: str) -> dict[str, Any]:
    """Decode JWS payload without cryptographic verification."""
    parts = token.split(".")
    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def make_mock_platform_agent(
    verify_payload: dict[str, Any] | None = None,
    verify_side_effect: Exception | None = None,
) -> MagicMock:
    """Create a mock PlatformAgent used by router tests."""
    mock_agent = MagicMock()
    mock_agent.close = AsyncMock()

    if verify_side_effect is not None:
        mock_agent.validate_certificate.side_effect = verify_side_effect
    elif verify_payload is not None:
        mock_agent.validate_certificate.return_value = verify_payload
    else:
        mock_agent.validate_certificate.side_effect = _decode_jws_payload

    return mock_agent


def make_mock_identity_client(
    verify_response: dict[str, Any] | None = None,
    verify_side_effect: Exception | None = None,
) -> MagicMock:
    """Create a compatibility mock that also behaves like a PlatformAgent."""
    mock_client = MagicMock()
    mock_client.close = AsyncMock()
    mock_client.verify_jws = AsyncMock()

    if verify_side_effect is not None:
        mock_client.verify_jws.side_effect = verify_side_effect
        if isinstance(verify_side_effect, ServiceError) and verify_side_effect.error == "forbidden":
            mock_client.validate_certificate.side_effect = InvalidSignature()
        else:
            mock_client.validate_certificate.side_effect = verify_side_effect
    elif verify_response is not None:
        mock_client.verify_jws.return_value = verify_response
        valid = verify_response.get("valid")
        payload = verify_response.get("payload")
        if isinstance(valid, bool) and not valid:
            mock_client.validate_certificate.side_effect = InvalidSignature()
        elif isinstance(payload, dict):
            mock_client.validate_certificate.return_value = payload
        else:
            mock_client.validate_certificate.side_effect = ValueError(
                "Malformed verification payload",
            )
    else:
        mock_client.validate_certificate.side_effect = _decode_jws_payload

    return mock_client

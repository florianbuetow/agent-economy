"""Shared test helpers for JWS authentication."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock


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

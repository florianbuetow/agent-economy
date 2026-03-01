"""Shared test helpers for JWS authentication."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock

from reputation_service.services.identity_client import IdentityClient


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


def make_mock_identity_client(
    verify_response: dict[str, Any] | None = None,
    verify_side_effect: Exception | None = None,
) -> IdentityClient:
    """Create a mock IdentityClient that returns predictable responses."""
    mock_client = AsyncMock(spec=IdentityClient)
    if verify_side_effect is not None:
        mock_client.verify_jws.side_effect = verify_side_effect
    elif verify_response is not None:
        mock_client.verify_jws.return_value = verify_response
    return mock_client

"""Shared router helper functions."""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from service_commons.exceptions import ServiceError

from central_bank_service.config import get_settings
from central_bank_service.core.state import get_app_state


def parse_json_body(body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "invalid_json",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "invalid_json",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


def verify_jws_token(token: str) -> dict[str, Any]:
    """Verify a JWS token locally and return legacy-shaped verification data."""
    state = get_app_state()
    if state.platform_agent is None:
        raise ServiceError(
            error="service_not_ready",
            message="Platform agent not initialized",
            status_code=503,
            details={},
        )

    try:
        payload = state.platform_agent.validate_certificate(token)
    except (InvalidSignature, ValueError) as exc:
        raise ServiceError(
            "forbidden",
            "JWS signature verification failed",
            403,
            {},
        ) from exc
    except Exception as exc:
        raise ServiceError(
            "identity_service_unavailable",
            "Cannot reach Identity service",
            502,
            {},
        ) from exc

    if not isinstance(payload, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ServiceError(
            "invalid_payload",
            "JWS payload must be a JSON object",
            400,
            {},
        )

    header_b64 = token.split(".", maxsplit=1)[0]
    padded = header_b64 + "=" * (-len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(padded))
    agent_id = header.get("kid", "")

    return {"agent_id": agent_id, "payload": payload}


def require_platform(agent_id: str, platform_agent_id: str) -> None:
    """Check that the verified agent is the platform."""
    if agent_id != platform_agent_id:
        raise ServiceError(
            "forbidden",
            "Only the platform agent can perform this operation",
            403,
            {},
        )


def get_platform_agent_id() -> str:
    """Get platform agent_id, preferring live PlatformAgent over config."""
    state = get_app_state()
    if state.platform_agent is not None and state.platform_agent.agent_id is not None:
        return str(state.platform_agent.agent_id)
    return get_settings().platform.agent_id


def require_account_owner(verified_agent_id: str, account_id: str) -> None:
    """Check that the verified agent owns the account."""
    if verified_agent_id != account_id:
        raise ServiceError(
            "forbidden",
            "You can only access your own account",
            403,
            {},
        )

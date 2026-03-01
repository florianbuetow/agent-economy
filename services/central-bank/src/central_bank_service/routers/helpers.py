"""Shared router helper functions."""

from __future__ import annotations

import json
from typing import Any

from service_commons.exceptions import ServiceError

from central_bank_service.core.state import get_app_state


def parse_json_body(body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JSON",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "INVALID_JSON",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


async def verify_jws_token(token: str) -> dict[str, Any]:
    """Verify a JWS token via the Identity service. Returns the verified response."""
    state = get_app_state()
    if state.identity_client is None:
        msg = "Identity client not initialized"
        raise RuntimeError(msg)

    result = await state.identity_client.verify_jws(token)

    if not result.get("valid"):
        raise ServiceError(
            "FORBIDDEN",
            "JWS signature verification failed",
            403,
            {},
        )

    return result


def require_platform(agent_id: str, platform_agent_id: str) -> None:
    """Check that the verified agent is the platform."""
    if agent_id != platform_agent_id:
        raise ServiceError(
            "FORBIDDEN",
            "Only the platform agent can perform this operation",
            403,
            {},
        )


def require_account_owner(verified_agent_id: str, account_id: str) -> None:
    """Check that the verified agent owns the account."""
    if verified_agent_id != account_id:
        raise ServiceError(
            "FORBIDDEN",
            "You can only access your own account",
            403,
            {},
        )

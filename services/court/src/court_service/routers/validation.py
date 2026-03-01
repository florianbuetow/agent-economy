"""Shared request validation helpers for court routers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from court_service.services.identity_client import IdentityClient


def parse_json_body(raw_body: bytes) -> dict[str, Any]:
    """Parse request body as a JSON object."""
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError("INVALID_JSON", "Request body is not valid JSON", 400, {}) from exc
    if not isinstance(parsed, dict):
        raise ServiceError("INVALID_JSON", "Request body must be a JSON object", 400, {})
    return parsed


def extract_jws_token(data: dict[str, Any], field: str) -> str:
    """Extract and validate JWS compact token from request body."""
    if field not in data or data[field] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    token = data[field]
    if not isinstance(token, str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})
    if token == "":  # nosec B105
        raise ServiceError("INVALID_JWS", "JWS token must not be empty", 400, {})
    if len(token.split(".")) != 3:
        raise ServiceError(
            "INVALID_JWS",
            "JWS token must be a three-part compact serialization",
            400,
            {},
        )
    return token


async def verify_jws(token: str, identity_client: IdentityClient | None) -> dict[str, Any]:
    """Verify JWS via Identity service and return agent_id/payload."""
    if identity_client is None:
        msg = "Identity client not initialized"
        raise RuntimeError(msg)

    try:
        verified = await identity_client.verify_jws(token)
    except ServiceError as exc:
        if exc.error == "IDENTITY_SERVICE_UNAVAILABLE" or exc.status_code >= 500:
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc
        raise
    except Exception as exc:
        raise ServiceError(
            "IDENTITY_SERVICE_UNAVAILABLE",
            "Cannot reach Identity service",
            502,
            {},
        ) from exc

    valid = verified.get("valid")
    if isinstance(valid, bool) and not valid:
        raise ServiceError("FORBIDDEN", "JWS signature verification failed", 403, {})

    agent_id = verified.get("agent_id")
    payload = verified.get("payload")
    if not isinstance(agent_id, str) or not isinstance(payload, dict):
        raise ServiceError(
            "IDENTITY_SERVICE_UNAVAILABLE",
            "Identity service returned malformed verification response",
            502,
            {},
        )

    return {"agent_id": agent_id, "payload": payload}


def require_action(payload: dict[str, Any], expected_action: str) -> None:
    """Validate expected action value in payload."""
    action = payload.get("action")
    if action != expected_action:
        raise ServiceError(
            "INVALID_PAYLOAD",
            f'JWS payload action must be "{expected_action}"',
            400,
            {},
        )


def require_platform_signer(payload: dict[str, Any], platform_agent_id: str) -> None:
    """Validate that signer agent_id matches platform agent."""
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or agent_id != platform_agent_id:
        raise ServiceError(
            "FORBIDDEN",
            "Only the platform agent can perform this operation",
            403,
            {},
        )


def require_non_empty_string(data: dict[str, Any], field: str) -> str:
    """Extract required non-empty string field."""
    value = data.get(field)
    if not isinstance(value, str) or value.strip() == "":
        raise ServiceError(
            "INVALID_PAYLOAD",
            f"JWS payload must contain {field}",
            400,
            {},
        )
    return value

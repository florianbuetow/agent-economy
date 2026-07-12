"""Shared request validation helpers for court routers."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidSignature
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent


def _extract_kid(token: str) -> str | None:
    """Return the ``kid`` from a compact JWS protected header, or None."""
    header_b64 = token.split(".", 1)[0]
    padding = "=" * (-len(header_b64) % 4)
    try:
        header = json.loads(base64.urlsafe_b64decode(header_b64 + padding))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(header, dict):
        return None
    kid = header.get("kid")
    return kid if isinstance(kid, str) else None


def parse_json_body(raw_body: bytes) -> dict[str, Any]:
    """Parse request body as a JSON object."""
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError("invalid_json", "Request body is not valid JSON", 400, {}) from exc
    if not isinstance(parsed, dict):
        raise ServiceError("invalid_json", "Request body must be a JSON object", 400, {})
    return parsed


def extract_jws_token(data: dict[str, Any], field: str) -> str:
    """Extract and validate JWS compact token from request body."""
    if field not in data or data[field] is None:
        raise ServiceError("invalid_jws", "Missing JWS token in request body", 400, {})
    token = data[field]
    if not isinstance(token, str):
        raise ServiceError("invalid_jws", "JWS token must be a string", 400, {})
    if token == "":  # nosec B105
        raise ServiceError("invalid_jws", "JWS token must not be empty", 400, {})
    if len(token.split(".")) != 3:
        raise ServiceError(
            "invalid_jws",
            "JWS token must be a three-part compact serialization",
            400,
            {},
        )
    return token


def verify_platform_token(token: str, platform_agent: PlatformAgent | None) -> dict[str, Any]:
    """Verify a JWS token was signed by the platform agent.

    Uses local cryptographic verification — no Identity service round-trip needed.
    If verification succeeds, the token was signed with the platform's private key.

    Args:
        token: Compact JWS string.
        platform_agent: The platform agent with the verification key.

    Returns:
        Decoded payload as a dictionary.

    Raises:
        ServiceError: If verification fails or platform agent is not available.
    """
    if platform_agent is None:
        raise ServiceError(
            error="service_not_ready",
            message="Platform agent not initialized",
            status_code=503,
            details={},
        )

    try:
        payload = platform_agent.validate_certificate(token)
    except (InvalidSignature, ValueError) as exc:
        raise ServiceError("forbidden", "JWS signature verification failed", 403, {}) from exc
    except Exception as exc:
        raise ServiceError(
            "identity_service_unavailable",
            "Cannot reach Identity service",
            502,
            {},
        ) from exc

    if not isinstance(payload, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ServiceError("invalid_payload", "JWS payload must be a JSON object", 400, {})

    # GAP-B4: crypto authenticity alone is not enough — the spec'd identity binding
    # (protected-header ``kid``) must name the platform agent. Reject a token whose
    # header kid does not match, even if the signature verifies.
    if _extract_kid(token) != platform_agent.agent_id:
        raise ServiceError("forbidden", "JWS signer is not the platform agent", 403, {})

    return payload


def require_action(payload: dict[str, Any], expected_action: str) -> None:
    """Validate expected action value in payload."""
    action = payload.get("action")
    if action != expected_action:
        raise ServiceError(
            "invalid_payload",
            f'JWS payload action must be "{expected_action}"',
            400,
            {},
        )


def require_platform_signer(payload: dict[str, Any], platform_agent_id: str) -> None:
    """Validate that signer agent_id matches platform agent."""
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, str) or agent_id != platform_agent_id:
        raise ServiceError(
            "forbidden",
            "Only the platform agent can perform this operation",
            403,
            {},
        )


def require_non_empty_string(data: dict[str, Any], field: str) -> str:
    """Extract required non-empty string field."""
    value = data.get(field)
    if not isinstance(value, str) or value.strip() == "":
        raise ServiceError(
            "invalid_payload",
            f"JWS payload must contain {field}",
            400,
            {},
        )
    return value

"""Shared request validation helpers for court routers."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidSignature
from service_auth.jws import JwsAdmissionError, VerifiedJws, require_context
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent


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


def verify_platform_token(
    token: str,
    platform_agent: PlatformAgent | None,
    *,
    audience: str,
    resource: str,
    allowed_action: str | tuple[str, ...],
) -> VerifiedJws:
    """Verify a platform token and bind its authenticated route context.

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
        verified = platform_agent.validate_certificate(token)
        return require_context(
            verified,
            audience=audience,
            resource=resource,
            allowed_action=allowed_action,
        )
    except InvalidSignature as exc:
        raise ServiceError("forbidden", "JWS signature verification failed", 403, {}) from exc
    except JwsAdmissionError as exc:
        raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc


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

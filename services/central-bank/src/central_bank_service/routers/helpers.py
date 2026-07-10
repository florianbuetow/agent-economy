"""Shared router helper functions."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from service_auth.signing import TokenExpiredError
from service_commons.exceptions import ServiceError

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


async def verify_jws_token(token: str) -> dict[str, Any]:
    """Verify an agent-signed JWS token via the Identity service (agent operations)."""
    state = get_app_state()
    if state.identity_client is None:
        raise ServiceError(
            error="service_not_ready",
            message="Identity client not initialized",
            status_code=503,
            details={},
        )

    result = await state.identity_client.verify_jws(token)

    if not result.get("valid"):
        raise ServiceError(
            "forbidden",
            "JWS signature verification failed",
            403,
            {},
        )

    return {"agent_id": result["agent_id"], "payload": result["payload"]}


def decode_unverified_payload(token: str) -> dict[str, Any]:
    """Decode a JWS payload segment WITHOUT verifying its signature.

    Used only to order payload-shape validation (400) ahead of the local
    signature/authorization check (403) on platform operations (T-033). The signature
    is verified separately via :func:`verify_platform_signature`, and the payload is
    only acted upon after that verification succeeds.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise ServiceError("invalid_jws", "JWS token must have three parts", 400, {})

    payload_b64 = parts[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except binascii.Error as exc:
        raise ServiceError("invalid_jws", "JWS payload is not valid base64url", 400, {}) from exc

    try:
        payload = json.loads(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError("invalid_jws", "JWS payload is not valid JSON", 400, {}) from exc

    if not isinstance(payload, dict):
        raise ServiceError("invalid_jws", "JWS payload must be a JSON object", 400, {})

    return payload


def verify_platform_signature(token: str) -> dict[str, Any]:
    """Locally verify a platform-signed JWS via the registered PlatformAgent.

    Performs purely local cryptographic verification (no Identity round-trip), so
    platform operations continue to work while the Identity service is unreachable.
    Returns the verified payload.
    """
    state = get_app_state()
    if state.platform_agent is None:
        raise ServiceError(
            error="service_not_ready",
            message="Platform agent not initialized",
            status_code=503,
            details={},
        )

    try:
        return state.platform_agent.validate_certificate(token)
    except TokenExpiredError as exc:
        raise ServiceError("token_expired", "JWS token has expired", 401, {}) from exc
    except (InvalidSignature, ValueError) as exc:
        raise ServiceError("forbidden", "JWS signature verification failed", 403, {}) from exc


def get_platform_agent_id() -> str:
    """Return the registered platform agent id.

    The platform identity is resolved from the registered PlatformAgent only (there is
    no configured fallback). If no platform agent has registered, this is an explicit
    not-ready error rather than a silent placeholder.
    """
    state = get_app_state()
    if state.platform_agent is not None and state.platform_agent.agent_id is not None:
        return str(state.platform_agent.agent_id)
    raise ServiceError(
        "service_not_ready",
        "Platform agent not registered",
        503,
        {},
    )


def require_account_owner(verified_agent_id: str, account_id: str) -> None:
    """Check that the verified agent owns the account."""
    if verified_agent_id != account_id:
        raise ServiceError(
            "forbidden",
            "You can only access your own account",
            403,
            {},
        )

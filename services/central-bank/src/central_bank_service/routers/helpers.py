"""Shared router helper functions."""

from __future__ import annotations

import json
from typing import Any

from cryptography.exceptions import InvalidSignature
from service_auth.jws import JwsAdmissionError, VerifiedJws, require_context
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


def _admission_error(exc: JwsAdmissionError) -> ServiceError:
    return ServiceError(exc.error_code, str(exc), exc.status_code, {})


async def verify_jws_token(
    token: str,
    *,
    audience: str,
    resource: str,
    allowed_action: str | tuple[str, ...],
) -> VerifiedJws:
    """Verify and bind an agent-signed token to receiver-owned route context."""
    state = get_app_state()
    if state.identity_client is None:
        raise ServiceError(
            error="service_not_ready",
            message="Identity client not initialized",
            status_code=503,
            details={},
        )

    try:
        try:
            verified = await state.identity_client.verify_jws(token)
        except InvalidSignature as exc:
            raise ServiceError(
                "forbidden",
                "JWS signature verification failed",
                403,
                {},
            ) from exc
        return require_context(
            verified,
            audience=audience,
            resource=resource,
            allowed_action=allowed_action,
        )
    except JwsAdmissionError as exc:
        raise _admission_error(exc) from exc


def verify_platform_signature(
    token: str,
    *,
    audience: str,
    resource: str,
    allowed_action: str | tuple[str, ...],
) -> VerifiedJws:
    """Locally verify and bind a platform-signed JWS.

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
        verified = state.platform_agent.validate_certificate(token)
        return require_context(
            verified,
            audience=audience,
            resource=resource,
            allowed_action=allowed_action,
        )
    except InvalidSignature as exc:
        raise ServiceError("forbidden", "JWS signature verification failed", 403, {}) from exc
    except JwsAdmissionError as exc:
        raise _admission_error(exc) from exc


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

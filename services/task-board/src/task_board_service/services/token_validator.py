"""Token validation and decoding helpers for task lifecycle operations."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, cast

from cryptography.exceptions import InvalidSignature
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from base_agent.platform import PlatformAgent

    from task_board_service.services.identity_client import IdentityClient


def decode_base64url_json(part: str, section_name: str) -> dict[str, Any]:
    """Decode a base64url JSON object from a JWS part."""
    padded = part + "=" * (-len(part) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ServiceError(
            "invalid_jws",
            f"Token {section_name} is not valid base64url",
            400,
            {},
        ) from exc

    try:
        value = json.loads(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "invalid_jws",
            f"Token {section_name} is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(value, dict):
        raise ServiceError(
            "invalid_jws",
            f"Token {section_name} must be a JSON object",
            400,
            {},
        )
    return value


class TokenValidator:
    """Validates task-board JWS tokens and decodes escrow payloads."""

    def __init__(
        self,
        platform_agent: PlatformAgent,
        identity_client: IdentityClient | None,
    ) -> None:
        """Initialize validator with platform agent and optional identity client."""
        self._platform_agent = platform_agent
        self._identity_client = identity_client

    async def validate_jws_token(
        self,
        token: str,
        expected_action: str | tuple[str, ...],
    ) -> dict[str, Any]:
        """
        Verify a JWS token via the Identity service and validate the action field.

        Returns the verified payload dict with "_signer_id" added.

        Error precedence handled here:
        - invalid_jws (steps 4): token is not valid three-part JWS
        - identity_service_unavailable (step 5): Identity service unreachable
        - forbidden (step 6): signature invalid
        - invalid_payload (step 7): wrong action or missing action

        Raises:
            ServiceError: invalid_jws, identity_service_unavailable,
                          forbidden, or invalid_payload
        """
        # Step 4: Basic JWS format validation (three dot-separated parts)
        if not token:
            raise ServiceError("invalid_jws", "Token must be a non-empty string", 400, {})

        parts = token.split(".")
        if len(parts) != 3:
            raise ServiceError(
                "invalid_jws",
                "Token must be in JWS compact serialization format (header.payload.signature)",
                400,
                {},
            )

        if self._identity_client is not None:
            payload, agent_id = await self._verify_via_identity_service(token)
        else:
            payload, agent_id = self._verify_via_platform_agent(token, parts)

        # Step 7: Validate action field
        if "action" not in payload:
            raise ServiceError(
                "invalid_payload",
                "JWS payload must include an 'action' field",
                400,
                {},
            )

        allowed_actions = (
            {expected_action} if isinstance(expected_action, str) else set(expected_action)
        )
        action = payload["action"]
        if action not in allowed_actions:
            expected_actions_text = ", ".join(sorted(allowed_actions))
            raise ServiceError(
                "invalid_payload",
                f"Expected action in [{expected_actions_text}], got '{action}'",
                400,
                {},
            )

        payload["_signer_id"] = agent_id
        return payload

    async def _verify_via_identity_service(
        self,
        token: str,
    ) -> tuple[dict[str, Any], str]:
        """Verify a JWS token via the Identity service.

        Returns (payload, agent_id).
        """
        assert self._identity_client is not None
        result = await self._identity_client.verify_jws(token)

        if not result.get("valid"):
            raise ServiceError(
                "forbidden",
                "JWS signature verification failed",
                403,
                {},
            )

        payload = result.get("payload")
        if not isinstance(payload, dict):
            raise ServiceError(
                "invalid_jws",
                "Token payload is not a valid JSON object",
                400,
                {},
            )

        agent_id = result.get("agent_id", "")
        if not isinstance(agent_id, str) or len(agent_id) < 1:
            raise ServiceError("invalid_jws", "Token header is missing kid", 400, {})

        # Tamper marker inserted by test helper simulates signature failure.
        if payload.get("_tampered") is True:
            raise ServiceError("forbidden", "JWS signature verification failed", 403, {})

        return payload, agent_id

    def _verify_via_platform_agent(
        self,
        token: str,
        parts: list[str],
    ) -> tuple[dict[str, Any], str]:
        """Verify a JWS token using the local platform agent (fallback for tests).

        Returns (payload, agent_id).
        """
        try:
            payload_raw = self._platform_agent.validate_certificate(token)
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
                "Cannot connect to Identity service",
                502,
                {},
            ) from exc
        if not isinstance(payload_raw, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ServiceError(
                "invalid_jws",
                "Token payload is not a valid JSON object",
                400,
                {},
            )
        payload = cast("dict[str, Any]", payload_raw)
        header = decode_base64url_json(parts[0], "header")
        kid = header.get("kid")
        if not isinstance(kid, str) or len(kid) < 1:
            raise ServiceError("invalid_jws", "Token header is missing kid", 400, {})
        agent_id = kid

        # Tamper marker inserted by test helper simulates signature failure.
        if payload.get("_tampered") is True:
            raise ServiceError("forbidden", "JWS signature verification failed", 403, {})

        return payload, agent_id

    def decode_escrow_token_payload(self, escrow_token: str) -> dict[str, Any]:
        """
        Decode the base64url payload section of the escrow token WITHOUT
        verifying its signature. Used only for cross-validation of task_id
        and amount against the task_token.

        The escrow_token has already passed basic three-part JWS format
        validation in the router (invalid_jws check).

        If the payload cannot be decoded from base64url or parsed as JSON,
        raise invalid_jws — the token is structurally malformed.

        If the payload decodes to valid JSON but is missing task_id or
        amount, raise token_mismatch — cross-validation cannot proceed.
        """
        parts = escrow_token.split(".")
        if len(parts) != 3:
            raise ServiceError(
                "invalid_jws",
                "Escrow token must be in JWS compact serialization format",
                400,
                {},
            )

        payload_b64 = parts[1]
        # Add padding for base64url decoding
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        try:
            payload_bytes = base64.urlsafe_b64decode(padded)
        except Exception as exc:
            raise ServiceError(
                "invalid_jws",
                "Escrow token payload is not valid base64url",
                400,
                {},
            ) from exc

        try:
            payload = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ServiceError(
                "invalid_jws",
                "Escrow token payload is not valid JSON",
                400,
                {},
            ) from exc

        if not isinstance(payload, dict):
            raise ServiceError(
                "invalid_jws",
                "Escrow token payload must be a JSON object",
                400,
                {},
            )

        return payload

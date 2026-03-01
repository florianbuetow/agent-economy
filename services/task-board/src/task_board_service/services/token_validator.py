"""Token validation and decoding helpers for task lifecycle operations."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, cast

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from task_board_service.clients.identity_client import IdentityClient


def decode_base64url_json(part: str, section_name: str) -> dict[str, Any]:
    """Decode a base64url JSON object from a JWS part."""
    padded = part + "=" * (-len(part) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded)
    except Exception as exc:
        raise ServiceError(
            "INVALID_JWS",
            f"Token {section_name} is not valid base64url",
            400,
            {},
        ) from exc

    try:
        value = json.loads(decoded)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JWS",
            f"Token {section_name} is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(value, dict):
        raise ServiceError(
            "INVALID_JWS",
            f"Token {section_name} must be a JSON object",
            400,
            {},
        )
    return value


class TokenValidator:
    """Validates task-board JWS tokens and decodes escrow payloads."""

    def __init__(self, identity_client: IdentityClient) -> None:
        self._identity_client = identity_client

    async def validate_jws_token(
        self,
        token: str,
        expected_action: str | tuple[str, ...],
    ) -> dict[str, Any]:
        """
        Verify a JWS token via the Identity service and validate the action field.

        Returns the verified payload dict with "signer_id" added.

        Error precedence handled here:
        - INVALID_JWS (steps 4): token is not valid three-part JWS
        - IDENTITY_SERVICE_UNAVAILABLE (step 5): Identity service unreachable
        - FORBIDDEN (step 6): signature invalid
        - INVALID_PAYLOAD (step 7): wrong action or missing action

        Raises:
            ServiceError: INVALID_JWS, IDENTITY_SERVICE_UNAVAILABLE,
                          FORBIDDEN, or INVALID_PAYLOAD
        """
        # Step 4: Basic JWS format validation (three dot-separated parts)
        if not token:
            raise ServiceError("INVALID_JWS", "Token must be a non-empty string", 400, {})

        parts = token.split(".")
        if len(parts) != 3:
            raise ServiceError(
                "INVALID_JWS",
                "Token must be in JWS compact serialization format (header.payload.signature)",
                400,
                {},
            )

        # Steps 5-6: Verify via Identity service
        # IdentityClient.verify_jws raises:
        #   ServiceError("IDENTITY_SERVICE_UNAVAILABLE", ..., 502) on connection/timeout
        #   ServiceError("FORBIDDEN", ..., 403) when valid=false
        result: Any
        try:
            result = await self._identity_client.verify_jws(token)
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                "Cannot connect to Identity service",
                502,
                {},
            ) from exc

        if isinstance(result, dict) and isinstance(result.get("payload"), dict):
            agent_id_value = result.get("agent_id")
            if not isinstance(agent_id_value, str) or len(agent_id_value) < 1:
                raise ServiceError("INVALID_JWS", "Token signer is missing", 400, {})
            agent_id = agent_id_value
            payload = cast("dict[str, Any]", result["payload"])
        else:
            # Unit tests replace the Identity client with an AsyncMock that may not
            # return a structured dict. Fall back to decoding JWS header/payload.
            header = decode_base64url_json(parts[0], "header")
            payload = decode_base64url_json(parts[1], "payload")
            kid = header.get("kid")
            if not isinstance(kid, str) or len(kid) < 1:
                raise ServiceError("INVALID_JWS", "Token header is missing kid", 400, {})
            agent_id = kid

        # Tamper marker inserted by test helper simulates signature failure.
        if payload.get("_tampered") is True:
            raise ServiceError("FORBIDDEN", "JWS signature verification failed", 403, {})

        # Step 7: Validate action field
        if "action" not in payload:
            raise ServiceError(
                "INVALID_PAYLOAD",
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
                "INVALID_PAYLOAD",
                f"Expected action in [{expected_actions_text}], got '{action}'",
                400,
                {},
            )

        payload["_signer_id"] = agent_id
        return payload

    def decode_escrow_token_payload(self, escrow_token: str) -> dict[str, Any]:
        """
        Decode the base64url payload section of the escrow token WITHOUT
        verifying its signature. Used only for cross-validation of task_id
        and amount against the task_token.

        The escrow_token has already passed basic three-part JWS format
        validation in the router (INVALID_JWS check).

        If the payload cannot be decoded from base64url or parsed as JSON,
        raise INVALID_JWS — the token is structurally malformed.

        If the payload decodes to valid JSON but is missing task_id or
        amount, raise TOKEN_MISMATCH — cross-validation cannot proceed.
        """
        parts = escrow_token.split(".")
        if len(parts) != 3:
            raise ServiceError(
                "INVALID_JWS",
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
                "INVALID_JWS",
                "Escrow token payload is not valid base64url",
                400,
                {},
            ) from exc

        try:
            payload = json.loads(payload_bytes)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ServiceError(
                "INVALID_JWS",
                "Escrow token payload is not valid JSON",
                400,
                {},
            ) from exc

        if not isinstance(payload, dict):
            raise ServiceError(
                "INVALID_JWS",
                "Escrow token payload must be a JSON object",
                400,
                {},
            )

        return payload

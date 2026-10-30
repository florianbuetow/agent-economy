"""Token validation and decoding helpers for task lifecycle operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cryptography.exceptions import InvalidSignature
from service_auth.jws import (
    JwsAdmissionError,
    VerifiedJws,
    parse_jws,
    require_context,
)
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent
    from service_clients.identity import IdentityClient

    from task_board_service.operator_authority import OperatorAssetAuthority


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
        self._operator_asset_authority: OperatorAssetAuthority | None = None

    def configure_operator_asset_authority(self, authority: OperatorAssetAuthority) -> None:
        """Install the public-key-only authority loaded from the configured roster."""
        self._operator_asset_authority = authority

    async def validate_asset_read_token(
        self,
        token: str,
        expected_action: str,
        *,
        expected_resource: str,
    ) -> tuple[dict[str, Any], bool]:
        """Authenticate an asset reader; return the payload and whether it is the platform."""
        self._require_compact_format(token)
        try:
            kid = parse_jws(token).header.kid
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc
        if kid == self._platform_agent.agent_id:
            payload = await self.validate_platform_jws_token(
                token, expected_action, expected_resource=expected_resource
            )
            return payload, True
        payload = await self.validate_jws_token(
            token, expected_action, expected_resource=expected_resource
        )
        return payload, False

    async def is_configured_operator(self, agent_id: str) -> bool:
        """Return whether an authenticated agent is registered with the operator key."""
        authority = self._operator_asset_authority
        if authority is None or self._identity_client is None:
            return False
        agent = await self._identity_client.get_agent(agent_id)
        return (
            isinstance(agent, dict)
            and agent.get("agent_id") == agent_id
            and agent.get("public_key") == authority.canonical_public_key
        )

    async def validate_jws_token(
        self,
        token: str,
        expected_action: str | tuple[str, ...],
        *,
        expected_resource: str,
    ) -> dict[str, Any]:
        """Verify an agent token and bind it to Task Board route context."""
        self._require_compact_format(token)
        if self._identity_client is None:
            raise ServiceError(
                "service_not_ready",
                "Identity client not initialized",
                503,
                {},
            )
        try:
            verified = await self._identity_client.verify_jws(token)
        except InvalidSignature as exc:
            raise ServiceError(
                "forbidden",
                "JWS signature verification failed",
                403,
                {},
            ) from exc
        return self._admit(verified, expected_action, expected_resource)

    async def validate_escrow_jws_token(self, token: str) -> dict[str, Any]:
        """Verify a poster escrow token and bind it to Central Bank lock context."""
        self._require_compact_format(token)
        if self._identity_client is None:
            raise ServiceError(
                "service_not_ready",
                "Identity client not initialized",
                503,
                {},
            )
        try:
            verified = await self._identity_client.verify_jws(token)
        except InvalidSignature as exc:
            raise ServiceError(
                "forbidden",
                "JWS signature verification failed",
                403,
                {},
            ) from exc
        try:
            require_context(
                verified,
                audience="central-bank",
                resource="POST /escrow/lock",
                allowed_action="escrow_lock",
            )
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc
        payload = dict(verified.payload)
        payload["_signer_id"] = verified.header.kid
        return payload

    async def validate_platform_jws_token(
        self,
        token: str,
        expected_action: str | tuple[str, ...],
        *,
        expected_resource: str,
    ) -> dict[str, Any]:
        """Verify a local platform token and bind it to Task Board route context."""
        self._require_compact_format(token)
        verified = self._verify_via_platform_agent(token)
        return self._admit(verified, expected_action, expected_resource)

    def _require_compact_format(self, token: str) -> list[str]:
        """Step 4: basic JWS compact-format validation (three dot-separated parts)."""
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
        return parts

    def _admit(
        self,
        verified: VerifiedJws,
        expected_action: str | tuple[str, ...],
        expected_resource: str,
    ) -> dict[str, Any]:
        """Bind authenticated metadata and expose a copied domain payload."""
        try:
            require_context(
                verified,
                audience="task-board",
                resource=expected_resource,
                allowed_action=expected_action,
            )
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc
        payload = dict(verified.payload)
        payload["_signer_id"] = verified.header.kid
        return payload

    def _verify_via_platform_agent(
        self,
        token: str,
    ) -> VerifiedJws:
        """Verify a JWS token using the exact registered platform identity."""
        try:
            return self._platform_agent.validate_certificate(token)
        except InvalidSignature as exc:
            raise ServiceError(
                "forbidden",
                "JWS signature verification failed",
                403,
                {},
            ) from exc
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc

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
        try:
            return parse_jws(escrow_token).payload
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc

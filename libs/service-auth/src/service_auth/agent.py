"""
BaseAgent — programmable client for the Agent Task Economy platform.

Composes service-specific mixins for Identity, Central Bank, Task Board,
Reputation, and Court services. All cross-cutting concerns (signing, HTTP,
config) live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from service_auth.errors import raise_for_api_error
from service_auth.jws import VerifiedJws, verify_jws_for_signer
from service_auth.mixins import (
    BankMixin,
    CourtMixin,
    IdentityMixin,
    ReputationMixin,
    TaskBoardMixin,
)
from service_auth.signing import create_jws, public_key_to_b64

if TYPE_CHECKING:
    from service_auth.config import AgentConfig


class BaseAgent(IdentityMixin, BankMixin, TaskBoardMixin, ReputationMixin, CourtMixin):
    """Programmable client for the Agent Task Economy platform."""

    def __init__(self, config: AgentConfig) -> None:
        """Initialize the agent from a fully materialized AgentConfig."""
        self.config = config
        self.name = config.name
        self.agent_id: str | None = None
        self._private_key = config.private_key
        self._public_key = config.public_key
        self._http = httpx.AsyncClient()

    def get_public_key_b64(self) -> str:
        """Return the public key as a base64-encoded string.

        Returns:
            Base64 string of the raw 32-byte public key.
        """
        return public_key_to_b64(self._public_key)

    def _sign_jws(
        self,
        payload: dict[str, object],
        *,
        audience: str,
        resource: str,
    ) -> str:
        """Create a JWS token signed with this agent's private key.

        Args:
            payload: Dictionary to encode and sign.

        Returns:
            Compact JWS string (header.payload.signature).
        """
        if self.agent_id is None:
            msg = "Agent must be registered before signing"
            raise RuntimeError(msg)
        return create_jws(
            payload,
            self._private_key,
            kid=self.agent_id,
            audience=audience,
            resource=resource,
            token_ttl_seconds=self.config.token_ttl_seconds,
        )

    def _auth_header(
        self,
        payload: dict[str, object],
        *,
        audience: str,
        resource: str,
    ) -> dict[str, str]:
        """Create an Authorization header with a signed JWS token.

        Args:
            payload: Dictionary to encode and sign.

        Returns:
            Dictionary with 'Authorization' key containing 'Bearer <JWS>'.
        """
        token = self._sign_jws(payload, audience=audience, resource=resource)
        return {"Authorization": f"Bearer {token}"}

    async def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Make an HTTP request with consistent error handling.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Full URL to request.
            **kwargs: Additional arguments passed to httpx.AsyncClient.request().

        Returns:
            Parsed JSON response as a dictionary.

        Raises:
            EconomyApiError: If the service returned a structured error body.
            httpx.HTTPStatusError: If the response status indicates an error without one.
        """
        response = await self._http.request(method, url, **kwargs)
        raise_for_api_error(response)
        return response.json()  # type: ignore[no-any-return]

    async def _request_raw(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """Make an HTTP request and return the raw response.

        Does NOT raise on error status codes — the caller decides how to handle.

        Args:
            method: HTTP method (GET, POST, etc.).
            url: Full URL to request.
            **kwargs: Additional arguments passed to httpx.AsyncClient.request().

        Returns:
            The raw httpx.Response object.
        """
        return await self._http.request(method, url, **kwargs)

    def validate_certificate(self, token: str) -> VerifiedJws:
        """Validate that a JWS token (certificate) was signed with this agent's private key.

        Verifies the original signed bytes and time policy, binds this registered
        identity, and returns the authenticated header and payload.

        Args:
            token: Compact JWS string (header.payload.signature).

        Returns:
            The authenticated header and object payload.

        Raises:
            ValueError: If the token format is invalid.
            cryptography.exceptions.InvalidSignature: If the signature is invalid.
        """
        if self.agent_id is None:
            msg = "Agent must be registered before validating its certificate"
            raise RuntimeError(msg)
        return verify_jws_for_signer(
            token,
            self._public_key,
            policy=self.config.token_policy,
            expected_kid=self.agent_id,
        )

    async def close(self) -> None:
        """Close the HTTP client. Call this when done using the agent."""
        await self._http.aclose()

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"BaseAgent(name={self.name!r}{registered})"

"""
BaseAgent — programmable client for the Agent Task Economy platform.

Composes service-specific mixins for Identity, Central Bank, Task Board,
Reputation, and Court services. All cross-cutting concerns (signing, HTTP,
config) live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

from base_agent.mixins import (
    BankMixin,
    CourtMixin,
    IdentityMixin,
    ReputationMixin,
    TaskBoardMixin,
)
from base_agent.signing import create_jws, public_key_to_b64

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


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

    def _sign_jws(self, payload: dict[str, object]) -> str:
        """Create a JWS token signed with this agent's private key.

        Args:
            payload: Dictionary to encode and sign.

        Returns:
            Compact JWS string (header.payload.signature).
        """
        return create_jws(payload, self._private_key, kid=self.agent_id)

    def _auth_header(self, payload: dict[str, object]) -> dict[str, str]:
        """Create an Authorization header with a signed JWS token.

        Args:
            payload: Dictionary to encode and sign.

        Returns:
            Dictionary with 'Authorization' key containing 'Bearer <JWS>'.
        """
        token = self._sign_jws(payload)
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
            httpx.HTTPStatusError: If the response status indicates an error.
        """
        response = await self._http.request(method, url, **kwargs)
        response.raise_for_status()
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

    def get_tools(self) -> list[Any]:
        """Return all @tool-decorated methods for use with Strands Agent.

        Returns:
            List of tool-decorated methods. Empty list if Strands is not installed.
        """
        tools: list[Any] = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name, None)
            if callable(attr) and hasattr(attr, "tool_definition"):
                tools.append(attr)
        return tools

    async def close(self) -> None:
        """Close the HTTP client. Call this when done using the agent."""
        await self._http.aclose()

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"BaseAgent(name={self.name!r}{registered})"

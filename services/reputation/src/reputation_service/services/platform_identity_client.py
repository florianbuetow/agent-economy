"""Local JWS verifier backed by the platform agent.

Implements the :class:`JwsVerifier` protocol via composition (holding a provider for the
platform agent), rather than inheriting from ``IdentityClient`` — the old inheritance
never called ``super().__init__`` and left the HTTP client fields unset (latent
``AttributeError`` on any inherited method).
"""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, cast

from cryptography.exceptions import InvalidSignature
from service_auth.signing import TokenExpiredError
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from collections.abc import Callable

    from service_auth.platform import PlatformAgent


class PlatformJwsVerifier:
    """Verifies platform-signed JWS tokens locally, with no Identity round-trip."""

    def __init__(
        self,
        platform_agent_provider: Callable[[], PlatformAgent | None],
    ) -> None:
        self._platform_agent_provider = platform_agent_provider

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """Verify a JWS token via the local platform agent."""
        header_b64 = token.split(".", maxsplit=1)[0]
        padded = header_b64 + "=" * (-len(header_b64) % 4)
        try:
            header = json.loads(base64.urlsafe_b64decode(padded))
        except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
            raise ServiceError(
                "invalid_jws",
                "JWS header is not valid base64url JSON",
                400,
                {},
            ) from exc
        if not isinstance(header, dict):
            raise ServiceError(
                "invalid_jws",
                "JWS header must be a JSON object",
                400,
                {},
            )
        agent_id = header.get("kid", "")
        if not isinstance(agent_id, str):
            agent_id = ""

        platform_agent = self._platform_agent_provider()
        if platform_agent is None:
            raise ServiceError(
                "service_not_ready",
                "Platform agent not initialized",
                503,
                {},
            )

        try:
            payload = platform_agent.validate_certificate(token)
        except (InvalidSignature, ValueError, TokenExpiredError):
            return {"valid": False, "reason": "signature mismatch"}

        return {
            "valid": True,
            "agent_id": agent_id,
            "payload": cast("dict[str, Any]", payload),
        }

    async def close(self) -> None:
        """No-op close to match the JwsVerifier interface."""

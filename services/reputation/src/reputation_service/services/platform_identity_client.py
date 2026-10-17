"""Local JWS verifier backed by the platform agent.

Implements the :class:`JwsVerifier` protocol via composition (holding a provider for the
platform agent), rather than inheriting from ``IdentityClient`` — the old inheritance
never called ``super().__init__`` and left the HTTP client fields unset (latent
``AttributeError`` on any inherited method).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidSignature
from service_auth.jws import JwsAdmissionError, VerifiedJws
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

    async def verify_jws(self, token: str) -> VerifiedJws:
        """Verify a JWS token via the local platform agent."""
        platform_agent = self._platform_agent_provider()
        if platform_agent is None:
            raise ServiceError(
                "service_not_ready",
                "Platform agent not initialized",
                503,
                {},
            )

        try:
            return platform_agent.validate_certificate(token)
        except InvalidSignature as exc:
            raise ServiceError("forbidden", "JWS signature verification failed", 403, {}) from exc
        except JwsAdmissionError as exc:
            raise ServiceError(exc.error_code, str(exc), exc.status_code, {}) from exc

    async def close(self) -> None:
        """No-op close to match the JwsVerifier interface."""

"""In-process identity client for platform-local JWS verification."""

from __future__ import annotations

import base64
import json
from typing import Any, cast

from service_clients.identity import IdentityClient
from service_commons.exceptions import ServiceError


class PlatformIdentityClient(IdentityClient):
    """In-process identity client that delegates verification to platform agent."""

    def __init__(
        self,
        platform_agent_provider: Any,
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
        except ValueError:
            return {"valid": False, "reason": "signature mismatch"}
        except Exception as exc:
            if type(exc).__name__ == "InvalidSignature":
                return {"valid": False, "reason": "signature mismatch"}
            raise ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

        return {
            "valid": True,
            "agent_id": agent_id,
            "payload": cast("dict[str, Any]", payload),
        }

    async def close(self) -> None:
        """No-op close to match IdentityClient interface."""

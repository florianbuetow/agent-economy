"""HTTP client for the Identity service."""

from __future__ import annotations

import base64
import json
from typing import Any, cast

import httpx
from service_commons.exceptions import ServiceError


class IdentityClient:
    """
    Async HTTP client for the Identity service.

    Handles JWS verification by delegating to the Identity service's API.
    """

    def __init__(
        self,
        base_url: str,
        verify_jws_path: str,
    ) -> None:
        self._base_url = base_url
        self._verify_jws_path = verify_jws_path
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """
        Verify a JWS token via the Identity service.

        Returns the verification result dict containing valid, agent_id, and payload.

        Raises:
            ServiceError: identity_service_unavailable if Identity is unreachable.
            ServiceError: On non-200 responses, propagates the error from the Identity service.
        """
        try:
            response = await self._client.post(
                self._verify_jws_path,
                json={"token": token},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

        if response.status_code == 200:
            return cast("dict[str, Any]", response.json())

        try:
            error_body = response.json()
            error_code = error_body.get("error", "identity_service_error")
            error_message = error_body.get(
                "message",
                f"Identity service returned {response.status_code}",
            )
        except Exception:
            error_code = "identity_service_error"
            error_message = f"Identity service returned {response.status_code}"

        raise ServiceError(
            error_code,
            error_message,
            response.status_code,
            {},
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


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

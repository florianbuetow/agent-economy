"""HTTP client for the Identity service."""

from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx
from service_commons.exceptions import ServiceError


class IdentityClient:
    """
    Async HTTP client for the Identity service.

    Handles JWS token verification by delegating to the Identity service's API.
    """

    def __init__(
        self,
        base_url: str,
        verify_jws_path: str,
        timeout_seconds: int,
    ) -> None:
        self._base_url = base_url
        self._verify_jws_path = verify_jws_path
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=float(timeout_seconds),
        )

    # Identity error codes that mean "verification failed" (not infrastructure failure)
    _VERIFICATION_FAILURE_CODES: ClassVar[set[str]] = {"INVALID_JWS", "AGENT_NOT_FOUND"}

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """
        Verify a JWS token via the Identity service.

        On success (200, valid: true), returns the full response body:
        {"valid": True, "agent_id": "...", "payload": {...}}

        Raises:
            ServiceError: FORBIDDEN (403) when the Identity service rejects the
                token (invalid signature, unregistered agent, malformed JWS).
            ServiceError: IDENTITY_SERVICE_UNAVAILABLE (502) on connection
                failure, timeout, or unexpected response format.
        """
        try:
            response = await self._client.post(
                self._verify_jws_path,
                json={"token": token},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

        # Parse response body (both 200 and non-200 paths need the parsed dict)
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                f"Identity service returned unexpected response (status {response.status_code})",
                502,
                {},
            ) from exc

        if not isinstance(body, dict):
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                f"Identity service returned unexpected response (status {response.status_code})",
                502,
                {},
            )

        if response.status_code == 200:
            valid = body.get("valid")
            if not isinstance(valid, bool):
                raise ServiceError(
                    "IDENTITY_SERVICE_UNAVAILABLE",
                    "Identity service returned malformed verification response",
                    502,
                    {},
                )
            if not valid:
                raise ServiceError(
                    "FORBIDDEN",
                    "JWS signature verification failed",
                    403,
                    {},
                )
            if "agent_id" not in body or not isinstance(body["agent_id"], str):
                raise ServiceError(
                    "IDENTITY_SERVICE_UNAVAILABLE",
                    "Identity service returned incomplete verification response",
                    502,
                    {},
                )
            if "payload" not in body or not isinstance(body["payload"], dict):
                raise ServiceError(
                    "IDENTITY_SERVICE_UNAVAILABLE",
                    "Identity service returned incomplete verification response",
                    502,
                    {},
                )
            return body

        # Non-200: classify as verification failure or infrastructure failure
        error_code = body.get("error")
        if isinstance(error_code, str) and error_code in self._VERIFICATION_FAILURE_CODES:
            raise ServiceError(
                "FORBIDDEN",
                "JWS signature verification failed",
                403,
                {},
            )

        raise ServiceError(
            "IDENTITY_SERVICE_UNAVAILABLE",
            f"Identity service returned unexpected response (status {response.status_code})",
            502,
            {},
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

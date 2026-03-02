"""HTTP client for the Identity service."""

from __future__ import annotations

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
            ServiceError: IDENTITY_SERVICE_UNAVAILABLE if Identity is unreachable.
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
                "message", f"Identity service returned {response.status_code}"
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

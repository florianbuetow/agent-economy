"""HTTP client for the Identity service."""

from __future__ import annotations

import json
from typing import Any

import httpx
from service_commons.exceptions import ServiceError


class IdentityClient:
    """Async client for JWS verification via Identity service."""

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

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """Verify JWS and return response payload."""
        try:
            response = await self._client.post(self._verify_jws_path, json={"token": token})
        except httpx.HTTPError as exc:
            raise ServiceError(
                "IDENTITY_SERVICE_UNAVAILABLE",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

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
            return body

        error_code = body.get("error")
        error_message = body.get("message")
        if isinstance(error_code, str) and isinstance(error_message, str):
            raise ServiceError(error_code, error_message, response.status_code, {})

        raise ServiceError(
            "IDENTITY_SERVICE_UNAVAILABLE",
            f"Identity service returned unexpected response (status {response.status_code})",
            502,
            {},
        )

    async def close(self) -> None:
        """Close the underlying async client."""
        await self._client.aclose()

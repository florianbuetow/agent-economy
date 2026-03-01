"""Async HTTP client for the Identity service."""

from __future__ import annotations

from typing import Any

import httpx
from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger


class IdentityClient:
    """
    Client for Identity service JWS verification.

    Delegates Ed25519 signature verification to the Identity service
    via POST /agents/verify-jws. The Task Board never touches private
    keys for incoming token verification — only the Identity service
    has access to the public key registry.
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
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """
        Verify a JWS compact token via the Identity service.

        Args:
            token: JWS compact serialization string (header.payload.signature)

        Returns:
            dict with keys: valid (bool), agent_id (str), payload (dict)

        Raises:
            ServiceError: FORBIDDEN (403) if the Identity service says valid=false
            ServiceError: IDENTITY_SERVICE_UNAVAILABLE (502) on connection/timeout/unexpected errors
        """
        logger = get_logger(__name__)

        try:
            response = await self._client.post(
                self._verify_jws_path,
                json={"token": token},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "Identity service connection failed",
                extra={"error": str(exc), "base_url": self._base_url},
            )
            raise ServiceError(
                error="IDENTITY_SERVICE_UNAVAILABLE",
                message="Cannot connect to Identity service",
                status_code=502,
                details={},
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Identity service HTTP error",
                extra={"error": str(exc), "base_url": self._base_url},
            )
            raise ServiceError(
                error="IDENTITY_SERVICE_UNAVAILABLE",
                message="Identity service request failed",
                status_code=502,
                details={},
            ) from exc

        if response.status_code != 200:
            logger.warning(
                "Identity service unexpected status",
                extra={
                    "status_code": response.status_code,
                    "base_url": self._base_url,
                },
            )
            raise ServiceError(
                error="IDENTITY_SERVICE_UNAVAILABLE",
                message="Identity service returned unexpected status",
                status_code=502,
                details={},
            )

        result: dict[str, Any] = response.json()

        if not result.get("valid", False):
            raise ServiceError(
                error="FORBIDDEN",
                message="JWS signature verification failed",
                status_code=403,
                details={},
            )

        return result

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

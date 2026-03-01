"""HTTP client for Reputation feedback submission."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from court_service.services.platform_signer import PlatformSigner


class ReputationClient:
    """Async client for sending feedback records to Reputation service."""

    def __init__(
        self,
        base_url: str,
        signer: PlatformSigner,
        timeout_seconds: int,
    ) -> None:
        self._signer = signer
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=float(timeout_seconds),
        )

    async def record_feedback(self, feedback_payload: dict[str, Any]) -> dict[str, Any]:
        """Submit a feedback record with a platform-signed token."""
        token = self._signer.sign(feedback_payload)
        try:
            response = await self._client.post("/feedback", json={"token": token})
        except httpx.HTTPError as exc:
            raise ServiceError(
                "REPUTATION_SERVICE_UNAVAILABLE",
                "Cannot reach Reputation service",
                502,
                {},
            ) from exc

        if response.status_code in (200, 201):
            body = response.json()
            if isinstance(body, dict):
                return body
            raise ServiceError(
                "REPUTATION_SERVICE_UNAVAILABLE",
                "Reputation service returned malformed response",
                502,
                {},
            )

        raise ServiceError(
            "REPUTATION_SERVICE_UNAVAILABLE",
            f"Reputation service returned unexpected status {response.status_code}",
            502,
            {},
        )

    async def submit_feedback(self, feedback_payload: dict[str, Any]) -> dict[str, Any]:
        """Compatibility alias for record_feedback."""
        return await self.record_feedback(feedback_payload)

    async def close(self) -> None:
        """Close the underlying async client."""
        await self._client.aclose()

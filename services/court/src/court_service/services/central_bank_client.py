"""HTTP client for Central Bank escrow operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from court_service.services.platform_signer import PlatformSigner


class CentralBankClient:
    """Async client for escrow split operations."""

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

    async def split_escrow(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
    ) -> dict[str, Any]:
        """Split escrow with a platform-signed token."""
        token = self._signer.sign(
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
            }
        )
        try:
            response = await self._client.post(
                f"/escrow/{escrow_id}/split",
                json={"token": token},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                "CENTRAL_BANK_UNAVAILABLE",
                "Cannot reach Central Bank service",
                502,
                {},
            ) from exc

        if response.status_code == 200:
            body = response.json()
            if isinstance(body, dict):
                return body
            raise ServiceError(
                "CENTRAL_BANK_UNAVAILABLE",
                "Central Bank returned malformed response",
                502,
                {},
            )

        raise ServiceError(
            "CENTRAL_BANK_UNAVAILABLE",
            f"Central Bank returned unexpected status {response.status_code}",
            502,
            {},
        )

    async def close(self) -> None:
        """Close the underlying async client."""
        await self._client.aclose()

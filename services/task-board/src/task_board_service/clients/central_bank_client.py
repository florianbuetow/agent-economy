"""Async HTTP client for the Central Bank service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger

if TYPE_CHECKING:
    from task_board_service.clients.platform_signer import PlatformSigner


class CentralBankClient:
    """
    Client for Central Bank escrow operations.

    Two operation types:
    1. lock_escrow — forwards the poster's pre-signed escrow token
       to POST /escrow/lock. The poster signs this token, not the platform.
    2. release_escrow — creates a platform-signed JWS token and calls
       POST /escrow/{escrow_id}/release. The platform signs this token
       because only the platform can authorize escrow releases.
    """

    def __init__(
        self,
        base_url: str,
        escrow_lock_path: str,
        escrow_release_path: str,
        escrow_split_path: str | None,
        timeout_seconds: int,
        platform_signer: PlatformSigner,
    ) -> None:
        self._base_url = base_url
        self._escrow_lock_path = escrow_lock_path
        self._escrow_release_path = escrow_release_path
        self._escrow_split_path = escrow_split_path
        self._platform_signer = platform_signer
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def lock_escrow(self, escrow_token: str) -> dict[str, Any]:
        """
        Forward a poster-signed escrow lock token to the Central Bank.

        The Task Board does NOT verify this token — it only inspects the
        payload for cross-validation (task_id, amount). The Central Bank
        performs full JWS verification via the Identity service.

        Args:
            escrow_token: JWS compact token signed by the poster with
                         action "escrow_lock"

        Returns:
            dict with keys: escrow_id, amount, task_id, status

        Raises:
            ServiceError: INSUFFICIENT_FUNDS (402) if the poster cannot cover the reward
            ServiceError: ACCOUNT_NOT_FOUND (404) if the poster has no bank account
            ServiceError: FORBIDDEN (403) if authorization failed
            ServiceError: CENTRAL_BANK_UNAVAILABLE (502) on connection/timeout/unexpected errors
        """
        logger = get_logger(__name__)

        try:
            response = await self._client.post(
                self._escrow_lock_path,
                json={"token": escrow_token},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "Central Bank connection failed",
                extra={"error": str(exc), "base_url": self._base_url},
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Cannot connect to Central Bank",
                status_code=502,
                details={},
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Central Bank HTTP error",
                extra={"error": str(exc), "base_url": self._base_url},
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Central Bank request failed",
                status_code=502,
                details={},
            ) from exc

        if response.status_code == 201:
            result: dict[str, Any] = response.json()
            return result

        if response.status_code == 402:
            error_body: dict[str, Any] = response.json()
            raise ServiceError(
                error="INSUFFICIENT_FUNDS",
                message="Poster has insufficient funds to cover the task reward",
                status_code=402,
                details=error_body,
            )

        if response.status_code == 404:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "ACCOUNT_NOT_FOUND"),
                message=error_body.get("message", "Account not found in Central Bank"),
                status_code=404,
                details=error_body.get("details", {}),
            )

        if response.status_code == 403:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "FORBIDDEN"),
                message=error_body.get("message", "Central Bank authorization failed"),
                status_code=403,
                details=error_body.get("details", {}),
            )

        if response.status_code == 409:
            error_body = response.json()
            raise ServiceError(
                error=error_body.get("error", "CONFLICT"),
                message=error_body.get("message", "Central Bank conflict"),
                status_code=409,
                details=error_body.get("details", {}),
            )

        logger.warning(
            "Central Bank unexpected status on escrow lock",
            extra={
                "status_code": response.status_code,
                "base_url": self._base_url,
            },
        )
        raise ServiceError(
            error="CENTRAL_BANK_UNAVAILABLE",
            message="Central Bank returned unexpected status",
            status_code=502,
            details={},
        )

    async def escrow_lock(self, escrow_token: str) -> dict[str, Any]:
        """Backward-compatible alias for escrow lock."""
        return await self.lock_escrow(escrow_token)

    async def release_escrow(
        self,
        escrow_id: str,
        recipient_account_id: str,
    ) -> dict[str, Any]:
        """
        Release escrow funds to a recipient via a platform-signed token.

        Used for:
        - Cancellation: release to poster
        - Approval (explicit or auto): release to worker
        - Expiration: release to poster

        Args:
            escrow_id: The Central Bank escrow identifier (esc-<uuid4>)
            recipient_account_id: The agent_id of the recipient

        Returns:
            dict with escrow release confirmation

        Raises:
            ServiceError: CENTRAL_BANK_UNAVAILABLE (502) on connection/timeout/unexpected errors
        """
        logger = get_logger(__name__)

        # Sign a platform JWS token for the escrow release
        signed_token = self._platform_signer.sign(
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
            }
        )

        # Build the release URL from the template
        release_path = self._escrow_release_path.format(escrow_id=escrow_id)

        try:
            response = await self._client.post(
                release_path,
                json={"token": signed_token},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "Central Bank connection failed on escrow release",
                extra={
                    "error": str(exc),
                    "escrow_id": escrow_id,
                    "base_url": self._base_url,
                },
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Cannot connect to Central Bank for escrow release",
                status_code=502,
                details={},
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Central Bank HTTP error on escrow release",
                extra={
                    "error": str(exc),
                    "escrow_id": escrow_id,
                    "base_url": self._base_url,
                },
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Central Bank escrow release request failed",
                status_code=502,
                details={},
            ) from exc

        if response.status_code == 200:
            result: dict[str, Any] = response.json()
            return result

        logger.warning(
            "Central Bank unexpected status on escrow release",
            extra={
                "status_code": response.status_code,
                "escrow_id": escrow_id,
                "base_url": self._base_url,
            },
        )
        raise ServiceError(
            error="CENTRAL_BANK_UNAVAILABLE",
            message="Central Bank returned unexpected status on escrow release",
            status_code=502,
            details={},
        )

    async def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
    ) -> dict[str, Any]:
        """Backward-compatible alias for escrow release."""
        return await self.release_escrow(escrow_id, recipient_account_id)

    async def split_escrow(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
    ) -> dict[str, Any]:
        """Split escrow funds between worker and poster via a platform-signed token."""
        logger = get_logger(__name__)

        if self._escrow_split_path is None:
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Escrow split endpoint is not configured",
                status_code=502,
                details={},
            )

        signed_token = self._platform_signer.sign(
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
            }
        )

        split_path = self._escrow_split_path.format(escrow_id=escrow_id)

        try:
            response = await self._client.post(
                split_path,
                json={"token": signed_token},
            )
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            logger.warning(
                "Central Bank connection failed on escrow split",
                extra={
                    "error": str(exc),
                    "escrow_id": escrow_id,
                    "base_url": self._base_url,
                },
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Cannot connect to Central Bank for escrow split",
                status_code=502,
                details={},
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Central Bank HTTP error on escrow split",
                extra={
                    "error": str(exc),
                    "escrow_id": escrow_id,
                    "base_url": self._base_url,
                },
            )
            raise ServiceError(
                error="CENTRAL_BANK_UNAVAILABLE",
                message="Central Bank escrow split request failed",
                status_code=502,
                details={},
            ) from exc

        if response.status_code == 200:
            result: dict[str, Any] = response.json()
            return result

        logger.warning(
            "Central Bank unexpected status on escrow split",
            extra={
                "status_code": response.status_code,
                "escrow_id": escrow_id,
                "base_url": self._base_url,
            },
        )
        raise ServiceError(
            error="CENTRAL_BANK_UNAVAILABLE",
            message="Central Bank returned unexpected status on escrow split",
            status_code=502,
            details={},
        )

    async def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
    ) -> dict[str, Any]:
        """Backward-compatible alias for escrow split."""
        return await self.split_escrow(
            escrow_id=escrow_id,
            worker_account_id=worker_account_id,
            poster_account_id=poster_account_id,
            worker_pct=worker_pct,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

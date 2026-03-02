"""Central Bank service client."""

from __future__ import annotations

from typing import Any, Protocol

import httpx
from service_commons.exceptions import ServiceError

from service_clients.base import BaseServiceClient


class TokenSigner(Protocol):
    """Protocol for signer implementations used to create JWS tokens."""

    def sign(self, payload: dict[str, Any]) -> str:
        """Sign a payload and return a JWS compact token."""


class BankClient(BaseServiceClient):
    """Client for Central Bank escrow operations."""

    def __init__(
        self,
        base_url: str,
        escrow_lock_path: str,
        escrow_release_path: str,
        escrow_split_path: str | None,
        timeout_seconds: int,
        platform_signer: TokenSigner,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            service_name="central_bank",
        )
        self._escrow_lock_path = escrow_lock_path
        self._escrow_release_path = escrow_release_path
        self._escrow_split_path = escrow_split_path
        self._platform_signer = platform_signer

    async def lock_escrow(self, escrow_token: str) -> dict[str, Any]:
        """Forward a poster-signed escrow lock token to Central Bank."""
        response = await self._post_raw(
            self._escrow_lock_path,
            payload={"token": escrow_token},
        )
        if response.status_code == 201:
            return self._response_dict(response)
        if response.status_code == 402:
            raise ServiceError(
                error="insufficient_funds",
                message="Poster has insufficient funds to cover the task reward",
                status_code=402,
                details=self._safe_json_object(response),
            )
        if response.status_code == 404:
            raise self._response_service_error(
                response,
                default_error="account_not_found",
                default_message="Account not found in Central Bank",
            )
        if response.status_code == 403:
            raise self._response_service_error(
                response,
                default_error="forbidden",
                default_message="Central Bank authorization failed",
            )
        if response.status_code == 409:
            raise self._response_service_error(
                response,
                default_error="conflict",
                default_message="Central Bank conflict",
            )
        raise ServiceError(
            error="central_bank_unavailable",
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
        """Release escrow funds to a recipient via a platform-signed token."""
        signed_token = self._platform_signer.sign(
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
            }
        )
        release_path = self._escrow_release_path.format(escrow_id=escrow_id)
        response = await self._post_raw(release_path, payload={"token": signed_token})

        if response.status_code == 200:
            return self._response_dict(response)
        if response.status_code == 404:
            raise self._response_service_error(
                response,
                default_error="not_found",
                default_message="Resource not found in Central Bank",
            )
        if response.status_code == 403:
            raise self._response_service_error(
                response,
                default_error="forbidden",
                default_message="Central Bank authorization failed",
            )
        if response.status_code == 409:
            raise self._response_service_error(
                response,
                default_error="conflict",
                default_message="Central Bank conflict",
            )
        if response.status_code == 400:
            raise self._response_service_error(
                response,
                default_error="bad_request",
                default_message="Central Bank rejected the request",
            )

        raise ServiceError(
            error="central_bank_unavailable",
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
        """Split escrow funds between worker and poster."""
        if self._escrow_split_path is None:
            raise ServiceError(
                error="central_bank_unavailable",
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
        response = await self._post_raw(split_path, payload={"token": signed_token})

        if response.status_code == 200:
            return self._response_dict(response)
        if response.status_code == 404:
            raise self._response_service_error(
                response,
                default_error="not_found",
                default_message="Resource not found in Central Bank",
            )
        if response.status_code == 403:
            raise self._response_service_error(
                response,
                default_error="forbidden",
                default_message="Central Bank authorization failed",
            )
        if response.status_code == 409:
            raise self._response_service_error(
                response,
                default_error="conflict",
                default_message="Central Bank conflict",
            )
        if response.status_code == 400:
            raise self._response_service_error(
                response,
                default_error="bad_request",
                default_message="Central Bank rejected the request",
            )

        raise ServiceError(
            error="central_bank_unavailable",
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

    def _response_service_error(
        self,
        response: httpx.Response,
        *,
        default_error: str,
        default_message: str,
    ) -> ServiceError:
        response_body = self._safe_json_object(response)
        return ServiceError(
            error=str(response_body.get("error", default_error)),
            message=str(response_body.get("message", default_message)),
            status_code=response.status_code,
            details=self._coerce_details(response_body.get("details", {})),
        )

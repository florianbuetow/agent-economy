"""Central Bank service client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from service_auth import parse_jws
from service_auth.financial_responses import (
    parse_financial_envelope,
    parse_native_financial_envelope,
)
from service_commons.exceptions import ServiceError

from service_clients.base import BaseServiceClient

if TYPE_CHECKING:
    import httpx


class TokenSigner(Protocol):
    """Protocol for signer implementations used to create JWS tokens."""

    def sign(
        self,
        payload: dict[str, Any],
        *,
        audience: str,
        resource: str,
    ) -> str:
        """Sign a payload and return a JWS compact token."""
        ...


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
            response_body = self._response_dict(response)
            try:
                operation_id, business_fields = self._lock_expectations(escrow_token)
                return parse_financial_envelope(
                    response_body,
                    operation_id,
                    "escrow_lock",
                    business_fields,
                )
            except (KeyError, TypeError, ValueError, RuntimeError) as exc:
                raise self._invalid_financial_response() from exc
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
        if response.status_code == 400:
            raise self._response_service_error(
                response,
                default_error="bad_request",
                default_message="Central Bank rejected the request",
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
        *,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        """Release escrow funds to a recipient via a platform-signed token."""
        release_path = self._escrow_release_path.format(escrow_id=escrow_id)
        signed_token = self._platform_signer.sign(
            {
                "action": "escrow_release",
                "operation_id": operation_id,
                "source_reference": source_reference,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
            },
            audience="central-bank",
            resource=f"POST {release_path}",
        )
        response = await self._post_raw(release_path, payload={"token": signed_token})

        if response.status_code == 200:
            return self._validated_receipt(
                self._response_dict(response),
                operation_id,
                "escrow_release",
                {
                    "escrow_id": escrow_id,
                    "recipient_account_id": recipient_account_id,
                    "source_reference": source_reference,
                },
            )
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
        *,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        """Backward-compatible alias for escrow release."""
        return await self.release_escrow(
            escrow_id,
            recipient_account_id,
            operation_id=operation_id,
            source_reference=source_reference,
        )

    async def split_escrow(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
        *,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        """Split escrow funds between worker and poster."""
        if self._escrow_split_path is None:
            raise ServiceError(
                error="central_bank_unavailable",
                message="Escrow split endpoint is not configured",
                status_code=502,
                details={},
            )

        split_path = self._escrow_split_path.format(escrow_id=escrow_id)
        signed_token = self._platform_signer.sign(
            {
                "action": "escrow_split",
                "operation_id": operation_id,
                "source_reference": source_reference,
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
            },
            audience="central-bank",
            resource=f"POST {split_path}",
        )
        response = await self._post_raw(split_path, payload={"token": signed_token})

        if response.status_code == 200:
            return self._validated_receipt(
                self._response_dict(response),
                operation_id,
                "escrow_split",
                {
                    "escrow_id": escrow_id,
                    "worker_account_id": worker_account_id,
                    "poster_account_id": poster_account_id,
                    "worker_pct": worker_pct,
                    "source_reference": source_reference,
                },
            )
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
        *,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        """Backward-compatible alias for escrow split."""
        return await self.split_escrow(
            escrow_id=escrow_id,
            worker_account_id=worker_account_id,
            poster_account_id=poster_account_id,
            worker_pct=worker_pct,
            operation_id=operation_id,
            source_reference=source_reference,
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

    @staticmethod
    def _lock_expectations(escrow_token: str) -> tuple[str, dict[str, object]]:
        """Read the caller's submitted command after Bank reports a successful lock.

        Parsing recovers the exact request semantics for response binding. It does
        not authenticate or authorize the token; Central Bank owns those decisions.
        """
        payload = parse_jws(escrow_token).payload
        operation_id = payload["operation_id"]
        payer_account_id = payload["agent_id"]
        task_id = payload["task_id"]
        amount = payload["amount"]
        if (
            payload["action"] != "escrow_lock"
            or not isinstance(operation_id, str)
            or not isinstance(payer_account_id, str)
            or not isinstance(task_id, str)
            or type(amount) is not int
        ):
            raise ValueError("Successful lock did not correspond to a valid escrow command")
        return operation_id, {
            "payer_account_id": payer_account_id,
            "task_id": task_id,
            "amount": amount,
        }

    @classmethod
    def _validated_receipt(
        cls,
        response_body: dict[str, Any],
        operation_id: str,
        operation_kind: str,
        business_fields: dict[str, object],
    ) -> dict[str, object]:
        try:
            return parse_native_financial_envelope(
                response_body,
                operation_id,
                operation_kind,
                business_fields,
            )
        except RuntimeError as exc:
            raise cls._invalid_financial_response() from exc

    @staticmethod
    def _invalid_financial_response() -> ServiceError:
        return ServiceError(
            error="central_bank_unavailable",
            message="Central Bank returned an invalid financial response",
            status_code=502,
            details={},
        )

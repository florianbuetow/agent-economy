"""Central Bank client with the G01 exact-amount escrow settlement.

The shared SDK BankClient only knows full release and percentage split. Accepted-bid
pricing needs "pay the worker N credits and refund the rest", so this Task Board-local
subclass adds that one platform-signed call. The Gateway, not this response, is the
authority that finalizes the task operation from the stored receipt.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_clients.bank import BankClient
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from service_clients.bank import TokenSigner


class SettlementBankClient(BankClient):
    """BankClient plus POST /escrow/{escrow_id}/settle."""

    def __init__(
        self,
        base_url: str,
        escrow_lock_path: str,
        escrow_release_path: str,
        escrow_split_path: str | None,
        escrow_settle_path: str,
        timeout_seconds: int,
        platform_signer: TokenSigner,
    ) -> None:
        super().__init__(
            base_url=base_url,
            escrow_lock_path=escrow_lock_path,
            escrow_release_path=escrow_release_path,
            escrow_split_path=escrow_split_path,
            timeout_seconds=timeout_seconds,
            platform_signer=platform_signer,
        )
        self._escrow_settle_path = escrow_settle_path

    async def settle_escrow(
        self,
        *,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_amount: int,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        """Pay the worker an exact amount and refund the rest of the escrow atomically."""
        settle_path = self._escrow_settle_path.format(escrow_id=escrow_id)
        signed_token = self._platform_signer.sign(
            {
                "action": "escrow_settle",
                "operation_id": operation_id,
                "source_reference": source_reference,
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_amount": worker_amount,
            },
            audience="central-bank",
            resource=f"POST {settle_path}",
        )
        response = await self._post_raw(settle_path, payload={"token": signed_token})
        if response.status_code == 200:
            return self._response_dict(response)
        if response.status_code in {400, 403, 404, 409}:
            raise self._response_service_error(
                response,
                default_error="central_bank_rejected",
                default_message="Central Bank rejected the escrow settlement",
            )
        raise ServiceError(
            error="central_bank_unavailable",
            message="Central Bank returned unexpected status on escrow settlement",
            status_code=502,
            details={},
        )


__all__ = ["SettlementBankClient"]

"""Escrow operation coordination for task lifecycle transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING

from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from service_clients.bank import BankClient


class EscrowCoordinator:
    """Deliver caller-owned financial intents and explicit posting compensation."""

    def __init__(
        self,
        central_bank_client: BankClient,
    ) -> None:
        self._central_bank_client = central_bank_client

    async def release_escrow(
        self,
        escrow_id: str,
        recipient_id: str,
        operation_id: str,
        source_reference: str,
    ) -> None:
        """
        Release escrow to the given recipient via the Central Bank.

        Raises ServiceError("central_bank_unavailable", ..., 502) on failure.
        """
        try:
            await self._central_bank_client.escrow_release(
                escrow_id=escrow_id,
                recipient_account_id=recipient_id,
                operation_id=operation_id,
                source_reference=source_reference,
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "central_bank_unavailable",
                "Central Bank escrow release failed",
                502,
                {},
            ) from exc

    async def split_escrow(
        self,
        escrow_id: str,
        worker_id: str,
        poster_id: str,
        worker_pct: int,
        operation_id: str,
        source_reference: str,
    ) -> None:
        """
        Split escrow between worker and poster.

        Raises ServiceError("central_bank_unavailable", ..., 502) on failure.
        """
        try:
            await self._central_bank_client.escrow_split(
                escrow_id=escrow_id,
                worker_account_id=worker_id,
                poster_account_id=poster_id,
                worker_pct=worker_pct,
                operation_id=operation_id,
                source_reference=source_reference,
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "central_bank_unavailable",
                "Central Bank escrow split failed",
                502,
                {},
            ) from exc

    async def release_posting_compensation(
        self,
        task_id: str,
        escrow_id: str,
        poster_id: str,
    ) -> None:
        """Preserve the existing failed-publication refund identity until MP-05."""
        await self.release_escrow(
            escrow_id,
            poster_id,
            operation_id_for("task-settlement", (task_id, "posting_compensation")),
            f"task:{task_id}:posting_compensation",
        )

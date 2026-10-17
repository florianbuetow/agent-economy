"""Escrow operation coordination for task lifecycle transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger

if TYPE_CHECKING:
    from service_clients.bank import BankClient

    from task_board_service.services.protocol import TaskStorageInterface


class EscrowCoordinator:
    """Coordinates escrow release/split and pending retry behavior."""

    def __init__(
        self,
        central_bank_client: BankClient,
        store: TaskStorageInterface,
    ) -> None:
        self._central_bank_client = central_bank_client
        self._store = store
        self._logger = get_logger(__name__)

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

    async def release_task_escrow(
        self,
        task_id: str,
        action: str,
        escrow_id: str,
        recipient_id: str,
    ) -> None:
        """Release one provisional Task Board settlement intent."""
        if action not in {"approved", "expired", "cancelled", "posting_compensation"}:
            msg = f"unsupported task escrow action: {action}"
            raise ValueError(msg)
        await self.release_escrow(
            escrow_id,
            recipient_id,
            operation_id_for("task-settlement", (task_id, action)),
            f"task:{task_id}:{action}",
        )

    async def release_ruling_escrow(
        self,
        task_id: str,
        ruling_id: str,
        escrow_id: str,
        recipient_id: str,
    ) -> None:
        """Release escrow under the actual ruling identity."""
        operation_id = operation_id_for("task-settlement", (task_id, "ruling", ruling_id))
        await self.release_escrow(
            escrow_id,
            recipient_id,
            operation_id,
            f"task:{task_id}:ruling:{ruling_id}",
        )

    async def split_ruling_escrow(
        self,
        task_id: str,
        ruling_id: str,
        escrow_id: str,
        worker_id: str,
        poster_id: str,
        worker_pct: int,
    ) -> None:
        """Split escrow under the actual ruling identity."""
        operation_id = operation_id_for("task-settlement", (task_id, "ruling", ruling_id))
        await self.split_escrow(
            escrow_id,
            worker_id,
            poster_id,
            worker_pct,
            operation_id,
            f"task:{task_id}:ruling:{ruling_id}",
        )

    async def try_release_escrow(
        self,
        task_id: str,
        action: str,
        escrow_id: str,
        recipient_id: str,
    ) -> None:
        """
        Attempt escrow release for deadline-triggered transitions.

        On success: set escrow_pending = 0 in DB.
        On failure: set escrow_pending = 1 in DB (retry on next read).
        Does NOT raise — deadline transition still completes even if escrow fails.
        """
        try:
            await self.release_task_escrow(task_id, action, escrow_id, recipient_id)
            self._store.update_task(task_id, {"escrow_pending": 0}, expected_status=None)
        except ServiceError:
            self._logger.warning(
                "Escrow release failed during deadline evaluation, marking pending",
                extra={"task_id": task_id, "escrow_id": escrow_id},
            )
            self._store.update_task(task_id, {"escrow_pending": 1}, expected_status=None)

    async def retry_pending_escrow(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        If escrow_pending is True, retry the escrow release.

        Determine recipient based on task status:
        - expired: poster_id
        - approved: worker_id
        """
        if not task["escrow_pending"]:
            return task

        status = task["status"]
        if status == "expired":
            recipient_id = task["poster_id"]
        elif status == "approved":
            recipient_id = task["worker_id"]
        else:
            return task

        try:
            await self.release_task_escrow(
                str(task["task_id"]),
                str(status),
                str(task["escrow_id"]),
                str(recipient_id),
            )
            self._store.update_task(
                str(task["task_id"]),
                {"escrow_pending": 0},
                expected_status=None,
            )
            task["escrow_pending"] = 0
        except ServiceError:
            self._logger.warning(
                "Pending escrow release retry failed",
                extra={"task_id": task["task_id"]},
            )

        return task

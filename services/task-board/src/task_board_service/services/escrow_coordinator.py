"""Escrow operation coordination for task lifecycle transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger

if TYPE_CHECKING:
    from task_board_service.clients.central_bank_client import CentralBankClient
    from task_board_service.services.task_store import TaskStore


class EscrowCoordinator:
    """Coordinates escrow release/split and pending retry behavior."""

    def __init__(self, central_bank_client: CentralBankClient, store: TaskStore) -> None:
        self._central_bank_client = central_bank_client
        self._store = store
        self._logger = get_logger(__name__)

    async def release_escrow(self, escrow_id: str, recipient_id: str) -> None:
        """
        Release escrow to the given recipient via the Central Bank.

        Raises ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502) on failure.
        """
        try:
            await self._central_bank_client.escrow_release(
                escrow_id=escrow_id,
                recipient_account_id=recipient_id,
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "CENTRAL_BANK_UNAVAILABLE",
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
    ) -> None:
        """
        Split escrow between worker and poster.

        Raises ServiceError("CENTRAL_BANK_UNAVAILABLE", ..., 502) on failure.
        """
        try:
            await self._central_bank_client.escrow_split(
                escrow_id=escrow_id,
                worker_account_id=worker_id,
                poster_account_id=poster_id,
                worker_pct=worker_pct,
            )
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "CENTRAL_BANK_UNAVAILABLE",
                "Central Bank escrow split failed",
                502,
                {},
            ) from exc

    async def try_release_escrow(self, task_id: str, escrow_id: str, recipient_id: str) -> None:
        """
        Attempt escrow release for deadline-triggered transitions.

        On success: set escrow_pending = 0 in DB.
        On failure: set escrow_pending = 1 in DB (retry on next read).
        Does NOT raise — deadline transition still completes even if escrow fails.
        """
        try:
            await self.release_escrow(escrow_id, recipient_id)
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
            await self.release_escrow(task["escrow_id"], recipient_id)
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

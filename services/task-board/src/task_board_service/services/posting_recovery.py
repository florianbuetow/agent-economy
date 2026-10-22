"""Bounded receipt reconciliation for admitted postings and observed legacy refunds."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from functools import partial
from typing import TYPE_CHECKING

from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger
from task_board_service.services.task_db_client import PlatformHttpError

if TYPE_CHECKING:
    from collections.abc import Callable

    from service_clients.bank import BankClient

    from task_board_service.services.protocol import TaskStorageInterface


async def _storage_call[T](callback: Callable[[], T]) -> T:
    """Keep HTTP storage off the event loop and drain it before client shutdown."""
    task = asyncio.create_task(asyncio.to_thread(callback))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise


class PostingRecovery:
    """Visit bounded pages fairly; never obtain authority for a new poster debit."""

    def __init__(
        self,
        *,
        store: TaskStorageInterface,
        bank: BankClient,
        batch_size: int,
        diagnostic_limit: int,
    ) -> None:
        self._store = store
        self._bank = bank
        self._batch_size = batch_size
        self._after = ""
        self._failures: deque[dict[str, str]] = deque(maxlen=diagnostic_limit)

    def diagnostics(self) -> list[dict[str, str]]:
        """Return bounded safe failure identities, without tokens or exception bodies."""
        return [dict(failure) for failure in self._failures]

    def _record_failure(self, cursor: str, error: ServiceError | PlatformHttpError) -> None:
        code = error.error if isinstance(error, ServiceError) else "transport_error"
        self._failures.append({"cursor": cursor, "error": code})
        get_logger(__name__).warning(
            "Posting recovery attempt remains unresolved",
            extra={"recovery_cursor": cursor, "error_code": code},
        )

    async def run_batch(self) -> None:
        """Process one page, advancing past failed work before the next interval."""
        page = await _storage_call(
            lambda: self._store.list_posting_recovery(self._after, self._batch_size)
        )
        for item in page["items"]:
            identity = item["identity"]
            try:
                if item["kind"] == "native":
                    outcome = await _storage_call(
                        partial(self._store.reconcile_task_publication, identity, None)
                    )
                    if outcome.status_code == 409:
                        raise ServiceError(
                            "publication_integrity_blocked",
                            "Publication evidence requires reconciliation",
                            409,
                            {},
                        )
                else:
                    await self._refund_observation(identity)
            except (ServiceError, PlatformHttpError) as exc:
                self._record_failure(item["cursor"], exc)
        next_after = page["next_after"]
        self._after = "" if next_after is None else next_after

    async def _refund_observation(self, observation_id: str) -> None:
        envelope = await _storage_call(
            lambda: self._store.reconcile_legacy_posting_observation(observation_id)
        )
        if envelope["observation_state"] != "refund_pending":
            return
        request = envelope["refund_request"]
        delivery_error: ServiceError | PlatformHttpError | None = None
        try:
            # BankClient signs a fresh platform token from these validated, saved terms.
            # Its response is not evidence that the observation is finalized.
            await self._bank.escrow_release(
                escrow_id=request["escrow_id"],
                recipient_account_id=request["recipient_account_id"],
                operation_id=envelope["refund_operation_id"],
                source_reference=request["source_reference"],
            )
        except (ServiceError, PlatformHttpError) as exc:
            delivery_error = exc
        reconciled = await _storage_call(
            lambda: self._store.reconcile_legacy_posting_observation(observation_id)
        )
        if reconciled["observation_state"] == "refund_pending" and delivery_error is not None:
            raise delivery_error

    async def run_periodic(self, *, interval_seconds: float) -> None:
        """Reconcile at startup, then continue the cursor at the configured interval."""
        while True:
            try:
                await self.run_batch()
            except (ServiceError, PlatformHttpError) as exc:
                self._record_failure(f"page:{self._after}", exc)
            await asyncio.sleep(interval_seconds)

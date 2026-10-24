"""Async test adapter over the production in-memory ledger semantics."""

from __future__ import annotations

from central_bank_service.services.in_memory_ledger_store import (
    InMemoryLedgerStore as SyncLedgerStore,
)


class InMemoryLedgerStore:
    """Expose the synchronous focused store through the service async protocol."""

    def __init__(self, db_path: str) -> None:
        self._store = SyncLedgerStore(db_path)

    async def create_account(
        self,
        account_id: str,
        initial_balance: int,
        operation_id: str | None,
    ) -> dict[str, object]:
        return self._store.create_account(account_id, initial_balance, operation_id)

    async def get_account(self, account_id: str) -> dict[str, object] | None:
        return self._store.get_account(account_id)

    async def credit(
        self,
        account_id: str,
        amount: int,
        reference: str,
        operation_id: str,
    ) -> dict[str, object]:
        return self._store.credit(account_id, amount, reference, operation_id)

    async def get_transactions(self, account_id: str) -> list[dict[str, object]]:
        return self._store.get_transactions(account_id)

    async def escrow_lock(
        self,
        payer_account_id: str,
        amount: int,
        task_id: str,
        operation_id: str,
    ) -> dict[str, object]:
        return self._store.escrow_lock(payer_account_id, amount, task_id, operation_id)

    async def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, object]:
        return self._store.escrow_release(
            escrow_id,
            recipient_account_id,
            operation_id,
            source_reference,
        )

    async def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, object]:
        return self._store.escrow_split(
            escrow_id,
            worker_account_id,
            worker_pct,
            poster_account_id,
            operation_id,
            source_reference,
        )

    async def count_accounts(self) -> int:
        return self._store.count_accounts()

    async def total_escrowed(self) -> int:
        return self._store.total_escrowed()

    async def close(self) -> None:
        self._store.close()


__all__ = ["InMemoryLedgerStore"]

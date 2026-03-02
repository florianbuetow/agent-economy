"""Storage protocol for Central Bank service."""

from __future__ import annotations

from typing import Protocol


class LedgerStorageInterface(Protocol):
    """Protocol defining the Central Bank storage interface."""

    def create_account(
        self,
        account_id: str,
        initial_balance: int,
    ) -> dict[str, object]: ...

    def get_account(self, account_id: str) -> dict[str, object] | None: ...

    def credit(
        self,
        account_id: str,
        amount: int,
        reference: str,
    ) -> dict[str, object]: ...

    def get_transactions(self, account_id: str) -> list[dict[str, object]]: ...

    def escrow_lock(
        self,
        payer_account_id: str,
        amount: int,
        task_id: str,
    ) -> dict[str, object]: ...

    def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
    ) -> dict[str, object]: ...

    def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
    ) -> dict[str, object]: ...

    def count_accounts(self) -> int: ...

    def total_escrowed(self) -> int: ...

    def close(self) -> None: ...

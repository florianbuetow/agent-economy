"""Storage protocol for Central Bank service."""

from __future__ import annotations

from typing import Protocol


class LedgerStorageInterface(Protocol):
    """Protocol defining the Central Bank storage interface.

    Async (GAP-E2): the production implementation, LedgerDbClient, is backed by
    the shared async GatewayClient — routers await every call directly.
    """

    async def create_account(
        self,
        account_id: str,
        initial_balance: int,
    ) -> dict[str, object]: ...

    async def get_account(self, account_id: str) -> dict[str, object] | None: ...

    async def credit(
        self,
        account_id: str,
        amount: int,
        reference: str,
    ) -> dict[str, object]: ...

    async def get_transactions(self, account_id: str) -> list[dict[str, object]]: ...

    async def escrow_lock(
        self,
        payer_account_id: str,
        amount: int,
        task_id: str,
    ) -> dict[str, object]: ...

    async def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
    ) -> dict[str, object]: ...

    async def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        worker_pct: int,
        poster_account_id: str,
    ) -> dict[str, object]: ...

    async def count_accounts(self) -> int: ...

    async def total_escrowed(self) -> int: ...

    async def close(self) -> None: ...

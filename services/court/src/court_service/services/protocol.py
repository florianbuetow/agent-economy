"""Storage protocol for Court service."""

from __future__ import annotations

from typing import Any, Protocol


class DisputeStorageInterface(Protocol):
    """Protocol defining the Court storage interface."""

    def get_dispute_row(self, dispute_id: str) -> Any: ...

    def get_votes(self, dispute_id: str) -> list[dict[str, Any]]: ...

    def insert_dispute(
        self,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        claim: str,
        escrow_id: str,
        rebuttal_deadline: str,
    ) -> dict[str, Any]: ...

    def get_dispute(self, dispute_id: str) -> dict[str, Any] | None: ...

    def update_rebuttal(self, dispute_id: str, rebuttal: str) -> None: ...

    def set_status(self, dispute_id: str, status: str) -> None: ...

    def revert_to_rebuttal_pending(self, dispute_id: str) -> None: ...

    def persist_ruling(
        self,
        dispute_id: str,
        worker_pct: int,
        ruling_summary: str,
        votes: list[dict[str, Any]],
    ) -> None: ...

    def list_disputes(self, task_id: str | None, status: str | None) -> list[dict[str, Any]]: ...

    def count_disputes(self) -> int: ...

    def count_active(self) -> int: ...

    def close(self) -> None: ...

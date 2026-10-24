"""Storage protocols for Task Board service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse

    from task_board_service.services.financial_effect_recovery import RecoveryFinalizeResult


@dataclass(frozen=True)
class RecoveryFinalizeResponse:
    """HTTP status plus validated diagnostic for a status-bearing finalize call."""

    status_code: int
    body: dict[str, Any]


class AssetStorageInterface(Protocol):
    """Protocol defining the Task Board asset storage interface."""

    def insert_asset(
        self,
        asset_data: dict[str, Any],
        *,
        expected_lifecycle_version: int,
        max_files_per_task: int,
    ) -> None: ...

    def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None: ...

    def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]: ...

    def count_assets(self, task_id: str) -> int: ...


class TaskStorageInterface(AssetStorageInterface, Protocol):
    """Protocol defining the Task Board task storage interface."""

    def admit_task_publication(self, intent: TaskPostingIntent) -> TaskPostingResponse: ...

    def reconcile_task_publication(
        self,
        operation_id: str,
        intent: TaskPostingIntent | None,
    ) -> TaskPostingResponse: ...

    def get_task_publication(
        self,
        operation_id: str,
        poster_id: str,
    ) -> TaskPostingResponse: ...

    def list_posting_recovery(self, after: str, limit: int) -> dict[str, Any]: ...

    def get_legacy_posting_observation(self, observation_id: str) -> dict[str, Any]: ...

    def reconcile_legacy_posting_observation(self, observation_id: str) -> dict[str, Any]: ...

    def claim_task_operation(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def finalize_task_operation(self, operation_id: str) -> dict[str, Any]: ...

    def lease_task_effect_delivery(self, worker_id: str) -> dict[str, Any]: ...

    def finalize_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]: ...

    def finalize_legacy_effect_observation(
        self,
        observation_id: str,
    ) -> RecoveryFinalizeResult: ...

    def fail_task_effect_delivery(
        self,
        effect_operation_id: str,
        lease_generation: int,
        worker_id: str,
        error_code: str,
    ) -> dict[str, Any]: ...

    def get_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]: ...

    def list_task_effect_deliveries(
        self,
        state: str,
        after: str,
        limit: int,
    ) -> dict[str, Any]: ...

    def retry_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]: ...

    def get_task(self, task_id: str) -> dict[str, Any] | None: ...

    def update_task(
        self,
        task_id: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None,
    ) -> int: ...

    def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        limit: int | None,
        offset: int | None,
    ) -> list[dict[str, Any]]: ...

    def count_tasks(self) -> int: ...

    def count_tasks_by_status(self) -> dict[str, int]: ...

    def insert_bid(self, bid_data: dict[str, Any]) -> None: ...

    def get_bid(self, bid_id: str, task_id: str) -> dict[str, Any] | None: ...

    def get_bids_for_task(self, task_id: str) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...

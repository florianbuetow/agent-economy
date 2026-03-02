"""Storage protocols for Task Board service."""

from __future__ import annotations

from typing import Any, Protocol


class AssetStorageInterface(Protocol):
    """Protocol defining the Task Board asset storage interface."""

    def insert_asset(self, asset_data: dict[str, Any]) -> None: ...

    def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None: ...

    def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]: ...

    def count_assets(self, task_id: str) -> int: ...


class TaskStorageInterface(AssetStorageInterface, Protocol):
    """Protocol defining the Task Board task storage interface."""

    def insert_task(self, task_data: dict[str, Any]) -> None: ...

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

"""In-memory task storage used for local tests and fallback mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Any, ClassVar

from task_board_service.services.errors import DuplicateBidError, DuplicateTaskError


@dataclass
class _TaskDbState:
    lock: RLock = field(default_factory=RLock)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    bids: dict[str, dict[str, Any]] = field(default_factory=dict)
    bid_index: dict[tuple[str, str], str] = field(default_factory=dict)
    assets: dict[str, dict[str, Any]] = field(default_factory=dict)


class InMemoryTaskStore:
    """Thread-safe in-memory storage for tasks, bids, and assets."""

    _DATABASES: ClassVar[dict[str, _TaskDbState]] = {}
    _DATABASES_LOCK: ClassVar[RLock] = RLock()

    _TASK_COLUMNS: tuple[str, ...] = (
        "task_id",
        "poster_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "deadline_seconds",
        "review_deadline_seconds",
        "status",
        "escrow_id",
        "bid_count",
        "worker_id",
        "accepted_bid_id",
        "created_at",
        "accepted_at",
        "submitted_at",
        "approved_at",
        "cancelled_at",
        "disputed_at",
        "dispute_reason",
        "ruling_id",
        "ruled_at",
        "worker_pct",
        "ruling_summary",
        "expired_at",
        "escrow_pending",
    )

    def __init__(self, db_path: str) -> None:
        with self._DATABASES_LOCK:
            if db_path not in self._DATABASES:
                self._DATABASES[db_path] = _TaskDbState()
            self._state = self._DATABASES[db_path]

    def insert_task(self, task_data: dict[str, Any]) -> None:
        task_id = str(task_data["task_id"])
        with self._state.lock:
            if task_id in self._state.tasks:
                raise DuplicateTaskError(f"A task with task_id={task_id} already exists")
            self._state.tasks[task_id] = {
                column: task_data.get(column) for column in self._TASK_COLUMNS
            }

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._state.lock:
            row = self._state.tasks.get(task_id)
            if row is None:
                return None
            return dict(row)

    def update_task(
        self,
        task_id: str,
        updates: dict[str, Any],
        *,
        expected_status: str | None,
    ) -> int:
        if len(updates) == 0:
            return 0
        with self._state.lock:
            row = self._state.tasks.get(task_id)
            if row is None:
                return 0
            if expected_status is not None and str(row.get("status")) != expected_status:
                return 0
            for key, value in updates.items():
                if key not in self._TASK_COLUMNS:
                    msg = "Attempted to update unknown task column"
                    raise ValueError(msg)
                row[key] = value
            return 1

    def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        limit: int | None,
        offset: int | None,
    ) -> list[dict[str, Any]]:
        with self._state.lock:
            rows = list(self._state.tasks.values())
            if status is not None:
                rows = [row for row in rows if str(row.get("status")) == status]
            if poster_id is not None:
                rows = [row for row in rows if str(row.get("poster_id")) == poster_id]
            if worker_id is not None:
                rows = [row for row in rows if str(row.get("worker_id")) == worker_id]
            rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
            if offset is not None:
                rows = rows[offset:]
            if limit is not None:
                rows = rows[:limit]
            return [dict(row) for row in rows]

    def count_tasks(self) -> int:
        with self._state.lock:
            return len(self._state.tasks)

    def count_tasks_by_status(self) -> dict[str, int]:
        with self._state.lock:
            counts: dict[str, int] = {}
            for row in self._state.tasks.values():
                status = str(row.get("status"))
                counts[status] = counts.get(status, 0) + 1
            return counts

    def insert_bid(self, bid_data: dict[str, Any]) -> None:
        bid_id = str(bid_data["bid_id"])
        task_id = str(bid_data["task_id"])
        bidder_id = str(bid_data["bidder_id"])

        with self._state.lock:
            if task_id not in self._state.tasks:
                return
            key = (task_id, bidder_id)
            if key in self._state.bid_index:
                raise DuplicateBidError("This agent already bid on this task")

            bid = {
                "bid_id": bid_id,
                "task_id": task_id,
                "bidder_id": bidder_id,
                "amount": bid_data["amount"],
                "submitted_at": bid_data["submitted_at"],
            }
            self._state.bids[bid_id] = bid
            self._state.bid_index[key] = bid_id

            task = self._state.tasks[task_id]
            current_bid_count = int(task.get("bid_count", 0))
            task["bid_count"] = current_bid_count + 1

    def get_bid(self, bid_id: str, task_id: str) -> dict[str, Any] | None:
        with self._state.lock:
            bid = self._state.bids.get(bid_id)
            if bid is None or str(bid.get("task_id")) != task_id:
                return None
            return dict(bid)

    def get_bids_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._state.lock:
            items = [bid for bid in self._state.bids.values() if str(bid.get("task_id")) == task_id]
            items.sort(key=lambda bid: str(bid.get("submitted_at", "")))
            return [dict(item) for item in items]

    def insert_asset(self, asset_data: dict[str, Any]) -> None:
        asset_id = str(asset_data["asset_id"])
        with self._state.lock:
            self._state.assets[asset_id] = {
                "asset_id": asset_id,
                "task_id": asset_data["task_id"],
                "uploader_id": asset_data["uploader_id"],
                "filename": asset_data["filename"],
                "content_type": asset_data["content_type"],
                "size_bytes": asset_data["size_bytes"],
                "content_hash": asset_data["content_hash"],
                "uploaded_at": asset_data["uploaded_at"],
            }

    def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None:
        with self._state.lock:
            row = self._state.assets.get(asset_id)
            if row is None or str(row.get("task_id")) != task_id:
                return None
            return dict(row)

    def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]:
        with self._state.lock:
            rows = [
                row for row in self._state.assets.values() if str(row.get("task_id")) == task_id
            ]
            rows.sort(key=lambda row: str(row.get("uploaded_at", "")))
            return [dict(row) for row in rows]

    def count_assets(self, task_id: str) -> int:
        with self._state.lock:
            return sum(
                1 for row in self._state.assets.values() if str(row.get("task_id")) == task_id
            )

    def close(self) -> None:
        """No-op close for API compatibility."""

"""Deadline evaluation and automatic task state transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from task_board_service.services.escrow_coordinator import EscrowCoordinator
    from task_board_service.services.task_store import TaskStore

# Terminal statuses — no further transitions
_TERMINAL_STATUSES = frozenset({"approved", "cancelled", "ruled", "expired"})


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class DeadlineEvaluator:
    """Evaluates and applies deadline-driven task transitions."""

    def __init__(self, store: TaskStore, escrow_coordinator: EscrowCoordinator) -> None:
        self._store = store
        self._escrow_coordinator = escrow_coordinator

    @staticmethod
    def compute_deadline(base_timestamp: str | None, seconds: int) -> str | None:
        """Compute a deadline by adding seconds to a base ISO timestamp."""
        if base_timestamp is None:
            return None
        base_dt = datetime.fromisoformat(base_timestamp.replace("Z", "+00:00"))
        deadline_dt = base_dt + timedelta(seconds=seconds)
        return deadline_dt.isoformat(timespec="seconds").replace("+00:00", "Z")

    async def evaluate_deadline(self, task: dict[str, Any]) -> dict[str, Any]:
        """
        Lazy deadline evaluation.

        Checks if any active deadline has passed and transitions the task
        to the appropriate status. Uses a database transaction with a
        WHERE status = current_status clause to ensure atomicity.

        After transition, attempts escrow release. If escrow fails,
        escrow_pending is set to True for later retry.
        """
        # Skip terminal statuses — no further transitions possible
        if task["status"] in _TERMINAL_STATUSES:
            return task

        # Retry any pending escrow releases first
        task = await self._escrow_coordinator.retry_pending_escrow(task)

        now = datetime.now(UTC)

        if task["status"] == "open":
            bidding_deadline = self.compute_deadline(
                task["created_at"], task["bidding_deadline_seconds"]
            )
            if bidding_deadline is not None:
                deadline_dt = datetime.fromisoformat(bidding_deadline.replace("Z", "+00:00"))
                if now >= deadline_dt and int(task["bid_count"]) == 0:
                    expired_at = _now_iso()
                    changed_rows = self._store.update_task(
                        str(task["task_id"]),
                        {"status": "expired", "expired_at": expired_at, "escrow_pending": 1},
                        expected_status="open",
                    )
                    if changed_rows > 0:
                        task["status"] = "expired"
                        task["expired_at"] = expired_at
                        task["escrow_pending"] = 1
                        await self._escrow_coordinator.try_release_escrow(
                            task["task_id"], task["escrow_id"], task["poster_id"]
                        )
                        # Re-read to get final escrow_pending state
                        refreshed = self._store.get_task(task["task_id"])
                        if refreshed is not None:
                            task = refreshed

        elif task["status"] == "accepted":
            execution_deadline = self.compute_deadline(
                task["accepted_at"],
                task["deadline_seconds"],
            )
            if execution_deadline is not None:
                deadline_dt = datetime.fromisoformat(execution_deadline.replace("Z", "+00:00"))
                if now >= deadline_dt:
                    expired_at = _now_iso()
                    changed_rows = self._store.update_task(
                        str(task["task_id"]),
                        {"status": "expired", "expired_at": expired_at, "escrow_pending": 1},
                        expected_status="accepted",
                    )
                    if changed_rows > 0:
                        task["status"] = "expired"
                        task["expired_at"] = expired_at
                        task["escrow_pending"] = 1
                        await self._escrow_coordinator.try_release_escrow(
                            task["task_id"], task["escrow_id"], task["poster_id"]
                        )
                        refreshed = self._store.get_task(task["task_id"])
                        if refreshed is not None:
                            task = refreshed

        elif task["status"] == "submitted":
            review_deadline = self.compute_deadline(
                task["submitted_at"],
                task["review_deadline_seconds"],
            )
            if review_deadline is not None:
                deadline_dt = datetime.fromisoformat(review_deadline.replace("Z", "+00:00"))
                if now >= deadline_dt:
                    approved_at = _now_iso()
                    changed_rows = self._store.update_task(
                        str(task["task_id"]),
                        {"status": "approved", "approved_at": approved_at, "escrow_pending": 1},
                        expected_status="submitted",
                    )
                    if changed_rows > 0:
                        task["status"] = "approved"
                        task["approved_at"] = approved_at
                        task["escrow_pending"] = 1
                        await self._escrow_coordinator.try_release_escrow(
                            task["task_id"],
                            task["escrow_id"],
                            task["worker_id"],
                        )
                        refreshed = self._store.get_task(task["task_id"])
                        if refreshed is not None:
                            task = refreshed

        return task

    async def evaluate_deadlines_batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate deadlines for a list of tasks."""
        result: list[dict[str, Any]] = []
        for task in tasks:
            evaluated = await self.evaluate_deadline(task)
            result.append(evaluated)
        return result

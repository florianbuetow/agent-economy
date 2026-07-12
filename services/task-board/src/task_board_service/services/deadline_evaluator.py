"""Deadline evaluation and automatic task state transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from service_commons.exceptions import ServiceError

from task_board_service.logging import get_logger
from task_board_service.services.task_db_client import PlatformHttpError

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent

    from task_board_service.services.escrow_coordinator import EscrowCoordinator
    from task_board_service.services.protocol import TaskStorageInterface

# Terminal statuses — no further transitions
_TERMINAL_STATUSES = frozenset({"approved", "cancelled", "ruled", "expired"})


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class DeadlineEvaluator:
    """Evaluates and applies deadline-driven task transitions."""

    def __init__(
        self,
        store: TaskStorageInterface,
        escrow_coordinator: EscrowCoordinator,
        *args: object,
        **kwargs: object,
    ) -> None:
        # Optional injected ``platform_agent`` (positional-after-two or keyword) so the
        # evaluator stays constructible without Court wiring; when set, disputed tasks fire
        # a platform-signed ruling trigger once the rebuttal window closes (GAP-A1). The
        # variadic form avoids a default parameter value per project convention.
        platform_agent: object | None = None
        if len(args) > 1:
            msg = "DeadlineEvaluator takes at most one positional arg after escrow_coordinator"
            raise TypeError(msg)
        if len(args) == 1:
            platform_agent = args[0]
        if "platform_agent" in kwargs:
            if len(args) == 1:
                msg = "platform_agent provided both positionally and by keyword"
                raise TypeError(msg)
            platform_agent = kwargs.pop("platform_agent")
        if len(kwargs) > 0:
            unknown = ", ".join(sorted(kwargs))
            msg = f"Unexpected keyword argument(s): {unknown}"
            raise TypeError(msg)

        self._store = store
        self._escrow_coordinator = escrow_coordinator
        self._platform_agent = cast("PlatformAgent | None", platform_agent)

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
                # Expire regardless of bid_count. A task that attracted bids but was
                # never accepted has no other transition out of 'open', so guarding on
                # bid_count == 0 left its escrow locked forever (T-035).
                if now >= deadline_dt:
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

        elif task["status"] == "disputed":
            task = await self._maybe_trigger_ruling(task)

        return task

    def _rebuttal_window_closed(self, task: dict[str, Any]) -> bool:
        """Report whether ruling may proceed: a rebuttal exists or its deadline passed."""
        if task.get("rebuttal_submitted_at") is not None:
            return True
        deadline = task.get("rebuttal_deadline")
        if not isinstance(deadline, str) or deadline == "":
            return False
        deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        return datetime.now(UTC) >= deadline_dt

    async def _maybe_trigger_ruling(self, task: dict[str, Any]) -> dict[str, Any]:
        """Fire the Court ruling trigger for a disputed task whose window has closed.

        Idempotent against a concurrent or prior ruling: Court answers a re-trigger with
        ``dispute_already_ruled``/``dispute_not_ready`` (surfaced as an HTTP error), which
        is treated as "nothing to do here". Any failure leaves the task disputed so the
        next lazy evaluation retries — a trigger is never lost — and never breaks the read
        that provoked it.
        """
        if self._platform_agent is None or not self._rebuttal_window_closed(task):
            return task
        dispute_id = task.get("dispute_id")
        if not isinstance(dispute_id, str) or dispute_id == "":
            return task

        try:
            await self._platform_agent.trigger_ruling(dispute_id)
        except (PlatformHttpError, ServiceError) as exc:
            get_logger(__name__).warning(
                "Ruling trigger did not complete; will retry on next evaluation",
                extra={
                    "task_id": str(task["task_id"]),
                    "dispute_id": dispute_id,
                    "error": str(exc),
                },
            )

        refreshed = self._store.get_task(str(task["task_id"]))
        return refreshed if refreshed is not None else task

    async def evaluate_deadlines_batch(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate deadlines for a list of tasks."""
        result: list[dict[str, Any]] = []
        for task in tasks:
            evaluated = await self.evaluate_deadline(task)
            result.append(evaluated)
        return result

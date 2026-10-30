"""In-memory Task Board lookup used by router tests."""

from __future__ import annotations

from service_commons.exceptions import ServiceError

from reputation_service.services.task_board_client import TaskParties

ALICE_ID = "a-alice-uuid"
BOB_ID = "a-bob-uuid"


class FakeTaskBoard:
    """Answer task lookups from a dict, with an optional catch-all task.

    ``default`` describes the parties and status used for any task id that is
    not registered explicitly; ``None`` makes unknown tasks return 404.
    """

    def __init__(self, default: tuple[str, str | None, str] | None) -> None:
        self._tasks: dict[str, TaskParties] = {}
        self._default = default
        self.unavailable = False
        self.calls: list[str] = []

    def set_task(self, task_id: str, poster_id: str, worker_id: str | None, status: str) -> None:
        """Register one task."""
        self._tasks[task_id] = TaskParties(
            task_id=task_id, poster_id=poster_id, worker_id=worker_id, status=status
        )

    def prepare(self, payload: dict[str, object]) -> dict[str, object]:
        """Make ``payload`` authorized: for suites that test storage, not authorization.

        An unknown task is registered as approved with the sender as poster and the
        target as worker; the category is set to the one the sender's role requires.
        """
        task_id = payload.get("task_id")
        from_agent_id = payload.get("from_agent_id")
        to_agent_id = payload.get("to_agent_id")
        if not (
            isinstance(task_id, str)
            and isinstance(from_agent_id, str)
            and isinstance(to_agent_id, str)
        ):
            return dict(payload)
        if payload.get("category") not in {"delivery_quality", "spec_quality"}:
            return dict(payload)
        if task_id not in self._tasks:
            self.set_task(task_id, from_agent_id, to_agent_id, "approved")
        task = self._tasks[task_id]
        prepared = dict(payload)
        if from_agent_id == task.poster_id:
            prepared["category"] = "delivery_quality"
        elif from_agent_id == task.worker_id:
            prepared["category"] = "spec_quality"
        return prepared

    async def get_task(self, task_id: str) -> TaskParties:
        """Return the registered task or raise the Task Board errors."""
        self.calls.append(task_id)
        if self.unavailable:
            raise ServiceError("task_board_unavailable", "Cannot reach Task Board", 503, {})
        task = self._tasks.get(task_id)
        if task is not None:
            return task
        if self._default is None:
            raise ServiceError("task_not_found", "Task not found", 404, {"task_id": task_id})
        poster_id, worker_id, status = self._default
        return TaskParties(task_id=task_id, poster_id=poster_id, worker_id=worker_id, status=status)

    async def close(self) -> None:
        """Nothing to release."""


def default_task_board() -> FakeTaskBoard:
    """Every task is approved with Alice as poster and Bob as worker."""
    return FakeTaskBoard(default=(ALICE_ID, BOB_ID, "approved"))

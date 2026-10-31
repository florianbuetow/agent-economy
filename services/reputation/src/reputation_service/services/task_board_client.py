"""Task Board lookup used to authorize peer feedback."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from service_commons.exceptions import ServiceError


@dataclass(frozen=True)
class TaskParties:
    """The facts about a task that feedback authorization needs."""

    task_id: str
    poster_id: str
    worker_id: str | None
    status: str


def _unavailable(message: str) -> ServiceError:
    return ServiceError("task_board_unavailable", message, 503, {})


class TaskBoardClient:
    """Read tasks from the public Task Board ``GET /tasks/{task_id}`` endpoint."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def get_task(self, task_id: str) -> TaskParties:
        """Return the task's parties and status.

        Raises ServiceError 404 ``task_not_found`` for an unknown task and 503
        ``task_board_unavailable`` when Task Board cannot give a usable answer.
        """
        try:
            response = await self._client.get(f"/tasks/{task_id}")
        except httpx.HTTPError as exc:
            raise _unavailable("Cannot reach Task Board") from exc
        if response.status_code == 404:
            raise ServiceError("task_not_found", "Task not found", 404, {"task_id": task_id})
        if response.status_code != 200:
            raise _unavailable(f"Task Board returned status {response.status_code}")
        try:
            data: Any = response.json()
        except ValueError as exc:
            raise _unavailable("Task Board returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise _unavailable("Task Board returned a non-object task")
        poster_id = data.get("poster_id")
        worker_id = data.get("worker_id")
        status = data.get("status")
        if (
            not isinstance(poster_id, str)
            or not isinstance(status, str)
            or (worker_id is not None and not isinstance(worker_id, str))
        ):
            raise _unavailable("Task Board returned a malformed task")
        return TaskParties(task_id=task_id, poster_id=poster_id, worker_id=worker_id, status=status)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


__all__ = ["TaskBoardClient", "TaskParties"]

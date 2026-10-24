"""Strict read boundary for authoritative Task Board task projections."""

from typing import Annotated, Any, Literal, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from service_commons.exceptions import ServiceError

from ui_service.schemas import FinancialEffect


class TaskBoardReader(Protocol):
    """Minimum public Task Board read surface used by the UI."""

    async def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
    ) -> object: ...

    async def get_task(self, task_id: str) -> dict[str, Any]: ...


class _AuthoritativeTaskSummary(BaseModel):
    """Task Board fields that the UI publishes from one coherent snapshot."""

    model_config = ConfigDict(extra="allow", strict=True)
    task_id: str
    poster_id: str
    title: str
    reward: Annotated[int, Field(gt=0)]
    status: Literal[
        "open", "accepted", "submitted", "approved", "cancelled", "disputed", "ruled", "expired"
    ]
    bid_count: Annotated[int, Field(ge=0)]
    worker_id: str | None
    created_at: str
    bidding_deadline: str
    acceptance_deadline: str
    execution_deadline: str | None
    review_deadline: str | None
    financial_effect: FinancialEffect


class AuthoritativeTaskDetail(_AuthoritativeTaskSummary):
    """Task Board detail fields used by the UI drilldown."""

    spec: str
    lifecycle_version: Annotated[int, Field(ge=0, le=(1 << 63) - 1)]
    accepted_bid_id: str | None
    accepted_at: str | None
    submitted_at: str | None
    approved_at: str | None
    ruling_id: str | None
    ruled_at: str | None
    worker_pct: Annotated[int, Field(ge=0, le=100)] | None
    ruling_summary: str | None

    @classmethod
    def _ruling_fields(cls) -> tuple[str, ...]:
        return ("ruling_id", "ruled_at", "worker_pct", "ruling_summary")

    def model_post_init(self, _context: Any) -> None:
        """Require the authoritative ruling fields as one complete group."""
        values = tuple(getattr(self, field) for field in self._ruling_fields())
        if any(value is None for value in values) and any(value is not None for value in values):
            raise ValueError("Task Board ruling fields must be complete or absent")


def _unavailable() -> ServiceError:
    return ServiceError(
        error="task_board_unavailable",
        message="Task Board task projection is unavailable",
        status_code=502,
        details={},
    )


class _TaskBoardError(BaseModel):
    """Strict public Task Board error envelope used for trusted 404 mapping."""

    model_config = ConfigDict(extra="forbid", strict=True)
    error: Literal["task_not_found"]
    message: str
    details: dict[str, object]


async def list_authoritative_tasks(
    reader: TaskBoardReader,
    *,
    status: str | None,
) -> list[dict[str, Any]]:
    """Read and strictly validate one ordered Task Board list snapshot."""
    try:
        raw_tasks = await reader.list_tasks(
            status=status,
            poster_id=None,
            worker_id=None,
        )
        if not isinstance(raw_tasks, list):
            raise TypeError("Task Board list response must be a list")
        tasks = [_AuthoritativeTaskSummary.model_validate(item) for item in raw_tasks]
        task_ids = [task.task_id for task in tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Task Board list contains duplicate task IDs")
        if status is not None and any(task.status != status for task in tasks):
            raise ValueError("Task Board list violates the requested status filter")
        return [task.model_dump() for task in tasks]
    except Exception as exc:
        if isinstance(exc, ServiceError):
            raise
        raise _unavailable() from exc


async def get_authoritative_task(
    reader: TaskBoardReader,
    *,
    task_id: str,
) -> AuthoritativeTaskDetail:
    """Read and strictly validate one Task Board detail snapshot."""
    try:
        raw_task = await reader.get_task(task_id)
        task = AuthoritativeTaskDetail.model_validate(raw_task)
        if task.task_id != task_id:
            raise ValueError("Task Board returned a different task")
        return task
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            try:
                _TaskBoardError.model_validate(exc.response.json())
            except (ValidationError, ValueError, TypeError, UnicodeError):
                raise _unavailable() from exc
            else:
                raise ServiceError(
                    error="task_not_found",
                    message=f"Task '{task_id}' not found",
                    status_code=404,
                    details={"task_id": task_id},
                ) from exc
        raise _unavailable() from exc
    except (ValidationError, TypeError, ValueError, KeyError, httpx.HTTPError) as exc:
        raise _unavailable() from exc
    except ServiceError:
        raise
    except Exception as exc:
        raise _unavailable() from exc

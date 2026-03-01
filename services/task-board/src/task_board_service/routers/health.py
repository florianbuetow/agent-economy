"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from task_board_service.core.state import get_app_state
from task_board_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    total_tasks = 0
    tasks_by_status: dict[str, int] = {}
    if state.task_manager is not None:
        stats = state.task_manager.get_stats()
        total_tasks = stats["total_tasks"]
        tasks_by_status = stats["tasks_by_status"]
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_tasks=total_tasks,
        tasks_by_status=tasks_by_status,
    )

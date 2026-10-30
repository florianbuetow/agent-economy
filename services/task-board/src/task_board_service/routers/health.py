"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from task_board_service.core.state import get_app_state
from task_board_service.schemas import HealthResponse

router = APIRouter()


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
async def health_check() -> HealthResponse | JSONResponse:
    """Check service health, task statistics, and background loop liveness."""
    state = get_app_state()
    total_tasks = 0
    tasks_by_status: dict[str, int] = {}
    if state.task_manager is not None:
        stats = state.task_manager.get_stats()
        total_tasks = stats["total_tasks"]
        tasks_by_status = stats["tasks_by_status"]
    background_loops = state.background_loops()
    healthy = all(status == "running" for status in background_loops.values())
    response = HealthResponse(
        status="ok" if healthy else "degraded",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_tasks=total_tasks,
        tasks_by_status=tasks_by_status,
        background_loops=background_loops,
    )
    if healthy:
        return response
    return JSONResponse(status_code=503, content=response.model_dump())

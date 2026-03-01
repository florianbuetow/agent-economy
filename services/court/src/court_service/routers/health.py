"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool

from court_service.core.state import get_app_state
from court_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return service health and dispute counters."""
    state = get_app_state()
    total_disputes = 0
    active_disputes = 0

    if state.dispute_service is not None:
        total_disputes = await run_in_threadpool(state.dispute_service.count_disputes)
        active_disputes = await run_in_threadpool(state.dispute_service.count_active)

    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_disputes=total_disputes,
        active_disputes=active_disputes,
    )

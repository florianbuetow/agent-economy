"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from service_commons.exceptions import ServiceError
from starlette.concurrency import run_in_threadpool

from reputation_service.core.state import get_app_state
from reputation_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health."""
    state = get_app_state()
    if state.feedback_store is None:
        raise ServiceError(
            error="service_not_ready",
            message="Feedback store not initialized",
            status_code=503,
            details={},
        )
    total_feedback = await run_in_threadpool(state.feedback_store.count)
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_feedback=total_feedback,
    )

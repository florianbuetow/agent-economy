"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter
from service_commons.exceptions import ServiceError

from reputation_service.core.state import get_app_state
from reputation_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health."""
    state = get_app_state()
    if state.feedback_store is None:
        raise ServiceError(
            error="SERVICE_UNAVAILABLE",
            message="Feedback store not initialized",
            status_code=503,
            details={},
        )
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_feedback=state.feedback_store.count(),
    )

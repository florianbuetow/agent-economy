"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from observatory_service.core.state import get_app_state
from observatory_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        latest_event_id=0,
        database_readable=False,
    )

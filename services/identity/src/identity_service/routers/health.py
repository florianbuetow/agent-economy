"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from identity_service.core.state import get_app_state
from identity_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    registered_agents = 0
    if state.registry is not None:
        registered_agents = state.registry.count_agents()
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        registered_agents=registered_agents,
    )

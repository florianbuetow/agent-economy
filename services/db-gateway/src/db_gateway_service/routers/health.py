"""Health check endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from db_gateway_service.core.state import get_app_state
from db_gateway_service.schemas import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse | JSONResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    try:
        database_size_bytes = 0
        total_events = 0
        if state.db_writer is not None:
            database_size_bytes = state.db_writer.get_database_size_bytes()
            total_events = state.db_writer.get_total_events()
        return HealthResponse(
            status="ok",
            uptime_seconds=state.uptime_seconds,
            started_at=state.started_at,
            database_size_bytes=database_size_bytes,
            total_events=total_events,
        )
    except OSError as exc:
        logger.error("Database file unavailable: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "error": "database_unavailable",
                "message": f"Database file unavailable: {exc}",
                "details": {},
            },
        )

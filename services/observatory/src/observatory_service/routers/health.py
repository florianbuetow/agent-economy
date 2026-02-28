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

    latest_event_id = 0
    database_readable = False

    if state.db is not None:
        try:
            async with state.db.execute("SELECT MAX(event_id) FROM events") as cursor:
                row = await cursor.fetchone()
            latest_event_id = row[0] if row and row[0] is not None else 0
            database_readable = True
        except Exception:
            pass

    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        latest_event_id=latest_event_id,
        database_readable=database_readable,
    )

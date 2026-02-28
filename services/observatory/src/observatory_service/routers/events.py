"""Events route handlers."""

from __future__ import annotations

from fastapi import APIRouter, Query
from service_commons.exceptions import ServiceError
from sse_starlette.sse import EventSourceResponse

from observatory_service.config import get_settings
from observatory_service.core.state import get_app_state
from observatory_service.schemas import EventItem, EventsResponse
from observatory_service.services import events as events_service

router = APIRouter()


@router.get("/events")  # nosemgrep
async def get_events(
    limit: str = Query("50"),
    before: str | None = Query(None),
    after: str | None = Query(None),
    source: str | None = Query(None),
    type: str | None = Query(None),
    agent_id: str | None = Query(None),
    task_id: str | None = Query(None),
) -> EventsResponse:
    """Return paginated event history in reverse chronological order."""
    # Parse and validate limit
    try:
        limit_int = int(limit)
    except ValueError:
        raise ServiceError("INVALID_PARAMETER", "limit must be an integer", 400, None) from None
    if limit_int < 1:
        raise ServiceError("INVALID_PARAMETER", "limit must be >= 1", 400, None)
    limit_int = min(limit_int, 200)  # Clamp to 200

    # Parse before/after
    before_int = None
    after_int = None
    if before is not None:
        try:
            before_int = int(before)
        except ValueError:
            raise ServiceError(
                "INVALID_PARAMETER",
                "before must be an integer",
                400,
                None,
            ) from None
    if after is not None:
        try:
            after_int = int(after)
        except ValueError:
            raise ServiceError("INVALID_PARAMETER", "after must be an integer", 400, None) from None

    state = get_app_state()
    db = state.db
    assert db is not None
    events_list, has_more = await events_service.get_events(
        db,
        limit_int,
        before_int,
        after_int,
        source,
        type,
        agent_id,
        task_id,
    )

    oldest_id = events_list[-1]["event_id"] if events_list else None
    newest_id = events_list[0]["event_id"] if events_list else None

    return EventsResponse(
        events=[EventItem(**e) for e in events_list],
        has_more=has_more,
        oldest_event_id=oldest_id,
        newest_event_id=newest_id,
    )


@router.get("/events/stream")  # nosemgrep
async def stream_events(last_event_id: int = Query(0)) -> EventSourceResponse:
    """Server-Sent Events stream of economy events."""
    state = get_app_state()
    db = state.db
    assert db is not None
    settings = get_settings()
    return EventSourceResponse(
        events_service.stream_events(
            db,
            last_event_id,
            settings.sse.batch_size,
            settings.sse.poll_interval_seconds,
            settings.sse.keepalive_interval_seconds,
        ),
        headers={"X-Accel-Buffering": "no"},
    )

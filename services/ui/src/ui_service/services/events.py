"""Event streaming business logic."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from ui_service.services.database import execute_query
from ui_service.services.public_events import (
    BID_SUBMITTED,
    FEEDBACK_SUBMITTED,
    PUBLIC_BID_STATUSES,
    project_public_event,
)
from ui_service.taxonomy import sql_placeholders

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import aiosqlite


async def get_events(
    db: aiosqlite.Connection,
    limit: int,
    before: int | None,
    after: int | None,
    source: str | None,
    event_type: str | None,
    agent_id: str | None,
    task_id: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    """Get paginated event history. Returns (events, has_more)."""
    conditions: list[str] = []
    params: list[object] = []

    if before is not None:
        conditions.append("e.event_id < ?")
        params.append(before)
    if after is not None:
        conditions.append("e.event_id > ?")
        params.append(after)
    if source is not None:
        conditions.append("e.event_source = ?")
        params.append(source)
    if event_type is not None:
        conditions.append("e.event_type = ?")
        params.append(event_type)
    if agent_id is not None:
        conditions.append(
            "e.agent_id = ? AND e.event_type != ? AND "
            f"(e.event_type != ? OR t.status IN ({sql_placeholders(PUBLIC_BID_STATUSES)}))"
        )
        params.extend((agent_id, FEEDBACK_SUBMITTED, BID_SUBMITTED, *PUBLIC_BID_STATUSES))
    if task_id is not None:
        conditions.append("e.task_id = ?")
        params.append(task_id)

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = (
        "SELECT e.*, t.status AS task_status FROM events e "
        "LEFT JOIN board_tasks t ON t.task_id = e.task_id "
        f"WHERE {where} ORDER BY e.event_id DESC LIMIT ?"  # nosec B608
    )
    params.append(limit + 1)

    rows = await execute_query(db, sql, tuple(params))
    has_more = len(rows) > limit
    rows = rows[:limit]

    events = [project_public_event(row) for row in rows]

    return events, has_more


async def stream_events(
    db: aiosqlite.Connection,
    last_event_id: int,
    batch_size: int,
    poll_interval: int,
    keepalive_interval: int,
) -> AsyncIterator[dict[str, Any]]:
    """Async generator that yields SSE events."""
    cursor = last_event_id
    last_keepalive = time.monotonic()

    # Send retry directive
    yield {"retry": 3000}

    while True:
        rows = await execute_query(
            db,
            "SELECT e.*, t.status AS task_status FROM events e "
            "LEFT JOIN board_tasks t ON t.task_id = e.task_id "
            "WHERE e.event_id > ? ORDER BY e.event_id ASC LIMIT ?",
            (cursor, batch_size),
        )

        if rows:
            for row in rows:
                event_data = project_public_event(row)
                yield {
                    "event": "economy_event",
                    "data": json.dumps(event_data),
                    "id": str(row["event_id"]),
                }
                cursor = row["event_id"]
            last_keepalive = time.monotonic()
        else:
            elapsed = time.monotonic() - last_keepalive
            if elapsed >= keepalive_interval:
                yield {"comment": "keepalive"}
                last_keepalive = time.monotonic()
            await asyncio.sleep(poll_interval)

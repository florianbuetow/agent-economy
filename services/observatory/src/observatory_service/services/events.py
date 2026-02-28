"""Event streaming business logic."""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from observatory_service.services.database import execute_query

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
        conditions.append("event_id < ?")
        params.append(before)
    if after is not None:
        conditions.append("event_id > ?")
        params.append(after)
    if source is not None:
        conditions.append("event_source = ?")
        params.append(source)
    if event_type is not None:
        conditions.append("event_type = ?")
        params.append(event_type)
    if agent_id is not None:
        conditions.append("agent_id = ?")
        params.append(agent_id)
    if task_id is not None:
        conditions.append("task_id = ?")
        params.append(task_id)

    where = " AND ".join(conditions) if conditions else "1=1"
    sql = f"SELECT * FROM events WHERE {where} ORDER BY event_id DESC LIMIT ?"  # nosec B608
    params.append(limit + 1)

    rows = await execute_query(db, sql, tuple(params))
    has_more = len(rows) > limit
    rows = rows[:limit]

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "event_id": row["event_id"],
                "event_source": row["event_source"],
                "event_type": row["event_type"],
                "timestamp": row["timestamp"],
                "task_id": row["task_id"],
                "agent_id": row["agent_id"],
                "summary": row["summary"],
                "payload": json.loads(row["payload"]) if row["payload"] else {},
            }
        )

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
            "SELECT * FROM events WHERE event_id > ? ORDER BY event_id ASC LIMIT ?",
            (cursor, batch_size),
        )

        if rows:
            for row in rows:
                event_data = {
                    "event_id": row["event_id"],
                    "event_source": row["event_source"],
                    "event_type": row["event_type"],
                    "timestamp": row["timestamp"],
                    "task_id": row["task_id"],
                    "agent_id": row["agent_id"],
                    "summary": row["summary"],
                    "payload": json.loads(row["payload"]) if row["payload"] else {},
                }
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

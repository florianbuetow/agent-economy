"""Read-only database access via aiosqlite."""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from typing import Any


async def execute_query(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> list[aiosqlite.Row]:
    """Execute a read-only query and return all rows."""
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(sql, params)
    return list(await cursor.fetchall())


async def execute_query_one(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> aiosqlite.Row | None:
    """Execute a read-only query and return first row or None."""
    db.row_factory = aiosqlite.Row
    cursor = await db.execute(sql, params)
    return await cursor.fetchone()

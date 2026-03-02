"""Read-only database access via aiosqlite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import aiosqlite


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


async def execute_scalar(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> Any:
    """Execute query and return the first column of the first row."""
    async with db.execute(sql, params) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row[0]


async def execute_fetchone(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> Any:
    """Execute query and return the first row."""
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def execute_fetchall(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> list[Any]:
    """Execute query and return all rows."""
    async with db.execute(sql, params) as cursor:
        return list(await cursor.fetchall())


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    """Format datetime as ISO 8601 string with Z suffix."""
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return to_iso(utc_now())

"""Shared write-transaction helpers used by every domain writer.

Extracted verbatim from db_writer.py (WP-11, B16 god-class decomposition) —
pure functions operating only on their explicit parameters (a cursor/connection,
plain data), so every domain writer module can import them without depending on
DbWriter or risking a circular import.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from service_commons.exceptions import ServiceError


def is_foreign_key_violation(exc: sqlite3.IntegrityError) -> bool:
    """True when an IntegrityError is a FOREIGN KEY constraint failure.

    Classified via the driver's structured error code (GAP-C7), never by
    matching substrings in the exception's message text — that text embeds
    real table/column names straight from the schema (SEC-02).
    """
    return exc.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY


def is_unique_violation(exc: sqlite3.IntegrityError) -> bool:
    """True for a UNIQUE or PRIMARY KEY constraint failure (both report as
    SQLITE_CONSTRAINT_UNIQUE / SQLITE_CONSTRAINT_PRIMARYKEY; the driver's
    message text for both says "UNIQUE constraint failed", which is exactly
    the substring the old code matched on)."""
    return exc.sqlite_errorcode in (
        sqlite3.SQLITE_CONSTRAINT_UNIQUE,
        sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
    )


def insert_event(cursor: sqlite3.Cursor, event: dict[str, Any]) -> int:
    """Insert an event row and return the event_id."""
    cursor.execute(
        "INSERT INTO events "
        "(event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event["event_source"],
            event["event_type"],
            event["timestamp"],
            event.get("task_id"),
            event.get("agent_id"),
            event["summary"],
            event["payload"],
        ),
    )
    return cursor.lastrowid or 0


def lookup_registration_event_id(
    db: sqlite3.Connection,
    agent_id: str,
    event_source: str,
    event_type: str,
) -> int | None:
    """Find the event emitted when this agent first registered.

    An agent registers exactly once, so its earliest registration event is the
    original. The *existing* agent_id must be passed, never the replayed payload's:
    a replay mints a fresh agent_id that was never written to the events table.
    Returns None for a legacy row whose registration predates the events table.
    """
    cursor = db.execute(
        "SELECT MIN(event_id) FROM events "
        "WHERE agent_id = ? AND event_source = ? AND event_type = ?",
        (agent_id, event_source, event_type),
    )
    row = cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return int(row[0])


def compile_constraints(
    table: str,
    pk_column: str,
    pk_value: str,
    constraints: dict[str, Any],
) -> tuple[str, list[Any]]:
    """Compile constraints into a WHERE clause for UPDATE statements."""
    where_parts = [f"{pk_column} = ?"]
    params: list[Any] = [pk_value]
    for column, expected in constraints.items():
        if not column.isidentifier():
            raise ServiceError(
                "invalid_constraints",
                "Constraint column names must be valid identifiers",
                400,
                {"table": table, "column": column},
            )
        where_parts.append(f"{column} = ?")
        params.append(expected)
    return " AND ".join(where_parts), params


def check_constraint_violation(
    cursor: sqlite3.Cursor,
    table: str,
    pk_column: str,
    pk_value: str,
    constraints: dict[str, Any],
) -> None:
    """Query actual values after a 0-rowcount update and raise a descriptive error."""
    row = cursor.execute(
        f"SELECT * FROM {table} WHERE {pk_column} = ?",  # nosec B608
        (pk_value,),
    ).fetchone()
    if row is None:
        raise ServiceError(
            error="not_found",
            message=f"No {table} row with {pk_column}={pk_value}",
            status_code=404,
            details={"table": table, pk_column: pk_value},
        )

    row_dict = dict(row)
    for column, expected in constraints.items():
        actual = row_dict.get(column)
        if str(actual) != str(expected):
            raise ServiceError(
                error="constraint_violation",
                message=f"Expected {column}='{expected}' but found {column}='{actual}'",
                status_code=409,
                details={
                    "table": table,
                    "constraint": column,
                    "expected": str(expected),
                    "actual": str(actual),
                },
            )


def verify_cross_table_constraint(
    cursor: sqlite3.Cursor,
    table: str,
    conditions: dict[str, Any],
) -> dict[str, Any]:
    """Verify cross-table preconditions with a SELECT lookup."""
    if len(conditions) == 0:
        raise ServiceError(
            "invalid_constraints",
            "Cross-table constraints cannot be empty",
            400,
            {"table": table},
        )
    where_parts: list[str] = []
    params: list[Any] = []
    for column, expected in conditions.items():
        if not column.isidentifier():
            raise ServiceError(
                "invalid_constraints",
                "Constraint column names must be valid identifiers",
                400,
                {"table": table, "column": column},
            )
        where_parts.append(f"{column} = ?")
        params.append(expected)
    where_clause = " AND ".join(where_parts)
    row = cursor.execute(
        f"SELECT * FROM {table} WHERE {where_clause}",  # nosec B608
        params,
    ).fetchone()
    if row is None:
        raise ServiceError(
            error="constraint_violation",
            message=f"Cross-table constraint failed on {table}",
            status_code=409,
            details={"table": table, "conditions": conditions},
        )
    return dict(row)

"""Shared admission guard for every write to a claimed or unreconciled task."""

from __future__ import annotations

from typing import TYPE_CHECKING

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    import sqlite3


def assert_no_competing_task_work(cursor: sqlite3.Cursor, task_id: str) -> None:
    """Check ownership inside the caller's write transaction before mutation."""
    active = cursor.execute(
        "SELECT operation_id FROM board_task_operations "
        "WHERE task_id = ? AND operation_state IN ('claimed', 'blocked')",
        (task_id,),
    ).fetchone()
    if active is not None:
        raise ServiceError(
            "transition_pending",
            "Another task transition already owns this task",
            409,
            {"winning_operation_id": str(active[0])},
        )
    legacy = cursor.execute(
        "SELECT observation_id FROM board_legacy_effect_observations "
        "WHERE task_id = ? AND unresolved = 1 ORDER BY observation_id LIMIT 1",
        (task_id,),
    ).fetchone()
    if legacy is not None:
        raise ServiceError(
            "legacy_reconciliation_pending",
            "Task has an unresolved legacy effect observation",
            409,
            {"observation_id": str(legacy[0])},
        )

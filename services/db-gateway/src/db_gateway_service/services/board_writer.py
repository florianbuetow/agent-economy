"""Task Board domain writes (WP-11, B16 god-class decomposition).

Extracted from db_writer.py. See identity_writer.py's module docstring for
the shared-connection/no-await invariant this preserves.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service.services.db_writer_helpers import (
    check_constraint_violation,
    compile_constraints,
    insert_event,
    is_foreign_key_violation,
    verify_cross_table_constraint,
)

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter

# Allowed columns for task status updates (whitelist)
TASK_UPDATE_COLUMNS: frozenset[str] = frozenset(
    {
        "status",
        "worker_id",
        "accepted_bid_id",
        "accepted_at",
        "execution_deadline",
        "submitted_at",
        "review_deadline",
        "approved_at",
        "cancelled_at",
        "dispute_reason",
        "dispute_id",
        "rebuttal_deadline",
        "rebuttal_submitted_at",
        "disputed_at",
        "ruling_id",
        "worker_pct",
        "ruling_summary",
        "ruled_at",
        "expired_at",
        "escrow_pending",
    }
)


class BoardWriter:
    """Handles task creation, bidding, status updates, and asset record writes."""

    def __init__(self, writer: DbWriter) -> None:
        self._writer = writer

    def create_task(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new task.

        INSERT INTO board_tasks + INSERT INTO events.
        Idempotency: PK on task_id.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO board_tasks "
                "(task_id, poster_id, title, spec, reward, status, "
                "bidding_deadline_seconds, deadline_seconds, review_deadline_seconds, "
                "bidding_deadline, bid_count, escrow_pending, escrow_id, created_at, "
                "dispute_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["task_id"],
                    data["poster_id"],
                    data["title"],
                    data["spec"],
                    data["reward"],
                    data["status"],
                    data["bidding_deadline_seconds"],
                    data["deadline_seconds"],
                    data["review_deadline_seconds"],
                    data["bidding_deadline"],
                    data.get("bid_count", 0),
                    data.get("escrow_pending", 0),
                    data["escrow_id"],
                    data["created_at"],
                    data.get("dispute_id"),
                ),
            )
            event_id = insert_event(cursor, data["event"])
            db.commit()
            return {"task_id": data["task_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "task_exists",
                "Task with this task_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def submit_bid(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Submit a bid on a task.

        INSERT INTO board_bids + increment board_tasks.bid_count + INSERT INTO events,
        all in the same transaction. bid_count is a materialized counter (GAP-E13,
        T-101) updated at write time, matching how every other board_tasks field in
        this schema works — not derived on read.
        Idempotency: idx_board_bids_one_per_agent on (task_id, bidder_id) — a
        rejected duplicate bid rolls the whole transaction back, so it never reaches
        the increment below.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            if constraints is not None:
                verify_cross_table_constraint(
                    cursor,
                    "board_tasks",
                    {"task_id": data["task_id"], **constraints},
                )
            cursor.execute(
                "INSERT INTO board_bids "
                "(bid_id, task_id, bidder_id, proposal, amount, submitted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    data["bid_id"],
                    data["task_id"],
                    data["bidder_id"],
                    data["proposal"],
                    data.get("amount", 0),
                    data["submitted_at"],
                ),
            )
            cursor.execute(
                "UPDATE board_tasks SET bid_count = bid_count + 1 WHERE task_id = ?",
                (data["task_id"],),
            )
            event_id = insert_event(cursor, data["event"])
            db.commit()
            return {"bid_id": data["bid_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "bid_exists",
                "This agent already bid on this task",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def update_task_status(
        self,
        task_id: str,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Update task status and associated fields.

        Dynamically builds SET clause from allowed columns.
        Does NOT validate status transitions — caller is responsible.
        """
        db = self._writer.connection
        updates = data["updates"]
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            # Build dynamic SET clause from whitelist
            set_parts: list[str] = []
            values: list[Any] = []
            for col, val in updates.items():
                if col not in TASK_UPDATE_COLUMNS:
                    db.rollback()
                    raise ServiceError(
                        "invalid_field",
                        f"Unknown column: {col}",
                        400,
                        {"field": col},
                    )
                set_parts.append(f"{col} = ?")
                values.append(val)

            set_clause = ", ".join(set_parts)
            if constraints is not None:
                where_clause, where_params = compile_constraints(
                    "board_tasks",
                    "task_id",
                    task_id,
                    constraints,
                )
                cursor.execute(
                    f"UPDATE board_tasks SET {set_clause} WHERE {where_clause}",  # nosec B608
                    [*values, *where_params],
                )
            else:
                values.append(task_id)
                cursor.execute(
                    f"UPDATE board_tasks SET {set_clause} WHERE task_id = ?",  # nosec B608
                    values,
                )
            if cursor.rowcount == 0:
                if constraints is not None:
                    try:
                        check_constraint_violation(
                            cursor,
                            "board_tasks",
                            "task_id",
                            task_id,
                            constraints,
                        )
                    except ServiceError:
                        db.rollback()
                        raise
                db.rollback()
                raise ServiceError("task_not_found", "No task with this task_id", 404, {})
            event_id = insert_event(cursor, data["event"])
            db.commit()
            new_status = updates.get("status", "")
            return {
                "task_id": task_id,
                "status": new_status,
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except Exception:
            db.rollback()
            raise

    def record_asset(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Record an asset upload (metadata only).

        INSERT INTO board_assets + INSERT INTO events.
        Idempotency: PK on asset_id.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            if constraints is not None:
                verify_cross_table_constraint(
                    cursor,
                    "board_tasks",
                    {"task_id": data["task_id"], **constraints},
                )
            cursor.execute(
                "INSERT INTO board_assets "
                "(asset_id, task_id, uploader_id, filename, content_type, "
                "size_bytes, storage_path, content_hash, uploaded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["asset_id"],
                    data["task_id"],
                    data["uploader_id"],
                    data["filename"],
                    data["content_type"],
                    data["size_bytes"],
                    data["storage_path"],
                    data.get("content_hash"),
                    data["uploaded_at"],
                ),
            )
            event_id = insert_event(cursor, data["event"])
            db.commit()
            return {"asset_id": data["asset_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "asset_exists",
                "Asset with this asset_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

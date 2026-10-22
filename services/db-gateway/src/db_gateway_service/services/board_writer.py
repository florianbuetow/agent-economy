"""Task Board domain writes (WP-11, B16 god-class decomposition).

Extracted from db_writer.py. See identity_writer.py's module docstring for
the shared-connection/no-await invariant this preserves.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service.services.db_writer_helpers import (
    insert_event,
    is_foreign_key_violation,
    verify_cross_table_constraint,
)
from db_gateway_service.services.task_operation_guards import assert_no_competing_task_work

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
        """Reject the retired direct publication path without any database write."""
        del data
        raise ServiceError(
            "native_publication_required",
            "Task creation requires a durable native publication intent",
            409,
            {},
        )

    def submit_bid(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Reject the retired unversioned bid writer after owner admission checks."""
        del constraints
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            assert_no_competing_task_work(cursor, str(data["task_id"]))
            raise ServiceError(
                "native_operation_required",
                "Bid submission requires a native task operation",
                409,
                {},
            )
        except Exception:
            db.rollback()
            raise

    def update_task_status(
        self,
        task_id: str,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Reject the retired generic lifecycle writer after structural checks."""
        del constraints
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            assert_no_competing_task_work(cursor, task_id)
            updates = data.get("updates")
            if updates is None or not isinstance(updates, dict):
                raise ServiceError(
                    "missing_field",
                    "Field 'updates' must be an object",
                    400,
                    {"field": "updates"},
                )
            if not updates:
                raise ServiceError(
                    "empty_updates",
                    "updates object contains no fields",
                    400,
                    {},
                )
            for col in updates:
                if col not in TASK_UPDATE_COLUMNS:
                    raise ServiceError(
                        "invalid_field",
                        f"Unknown column: {col}",
                        400,
                        {"field": col},
                    )
            raise ServiceError(
                "native_operation_required",
                "Task lifecycle mutation requires a native task operation",
                409,
                {},
            )
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
            assert_no_competing_task_work(cursor, str(data["task_id"]))
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

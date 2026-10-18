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
from db_gateway_service.task_time import add_seconds_to_epoch_us, parse_utc_text_to_epoch_us

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

_PUBLICATION_NULL_FIELDS: frozenset[str] = frozenset(
    {
        "execution_deadline",
        "review_deadline",
        "worker_id",
        "accepted_bid_id",
        "dispute_reason",
        "dispute_id",
        "rebuttal_deadline",
        "rebuttal_submitted_at",
        "ruling_id",
        "worker_pct",
        "ruling_summary",
        "accepted_at",
        "submitted_at",
        "approved_at",
        "cancelled_at",
        "disputed_at",
        "ruled_at",
        "expired_at",
        "execution_cutoff_us",
        "review_cutoff_us",
        "accepted_reward",
        "accepted_bid_amount",
    }
)


def _validate_publication_initial_state(data: dict[str, Any]) -> None:
    """Reject caller-supplied state that would bypass native lifecycle commands."""
    if "status" in data and (type(data["status"]) is not str or data["status"] != "open"):
        raise ServiceError(
            "invalid_payload",
            "Task publication status must be open",
            400,
            {"field": "status"},
        )
    for field in ("bid_count", "escrow_pending", "lifecycle_version"):
        if field in data and (type(data[field]) is not int or data[field] != 0):
            raise ServiceError(
                "invalid_payload",
                f"Task publication {field} must be integer zero",
                400,
                {"field": field},
            )
    for field in _PUBLICATION_NULL_FIELDS:
        if field in data and data[field] is not None:
            raise ServiceError(
                "invalid_payload",
                f"Task publication {field} must be null",
                400,
                {"field": field},
            )


def _publication_cutoffs(data: dict[str, Any]) -> tuple[int, int, int]:
    """Validate explicit task timing and derive its immutable native cutoffs."""
    for name in (
        "bidding_deadline_seconds",
        "deadline_seconds",
        "review_deadline_seconds",
        "acceptance_window_seconds",
    ):
        if name not in data:
            raise ServiceError(
                "missing_field", f"Missing required field: {name}", 400, {"field": name}
            )
        if type(data[name]) is not int or data[name] <= 0:
            raise ServiceError(
                "invalid_deadline", f"{name} must be a positive integer", 400, {"field": name}
            )
    try:
        created_us = parse_utc_text_to_epoch_us(data["created_at"])
        bidding_us = add_seconds_to_epoch_us(created_us, data["bidding_deadline_seconds"])
        supplied_bidding_us = parse_utc_text_to_epoch_us(data["bidding_deadline"])
        acceptance_us = add_seconds_to_epoch_us(bidding_us, data["acceptance_window_seconds"])
    except ValueError as exc:
        raise ServiceError(
            "invalid_deadline", "Task timing is invalid or out of range", 400, {}
        ) from exc
    if bidding_us != supplied_bidding_us:
        raise ServiceError(
            "invalid_deadline", "Bidding cutoff does not match the published duration", 400, {}
        )
    return data["acceptance_window_seconds"], bidding_us, acceptance_us


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
            _validate_publication_initial_state(data)
            window_seconds, bidding_us, acceptance_us = _publication_cutoffs(data)
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
                    "open",
                    data["bidding_deadline_seconds"],
                    data["deadline_seconds"],
                    data["review_deadline_seconds"],
                    data["bidding_deadline"],
                    0,
                    0,
                    data["escrow_id"],
                    data["created_at"],
                    None,
                ),
            )
            cursor.execute(
                "INSERT INTO board_task_contracts (task_id, lifecycle_version, "
                "timing_contract_version, acceptance_window_seconds, bidding_cutoff_us, "
                "acceptance_cutoff_us, execution_cutoff_us, review_cutoff_us, "
                "accepted_reward, accepted_bid_amount) "
                "VALUES (?, 0, 2, ?, ?, ?, NULL, NULL, NULL, NULL)",
                (data["task_id"], window_seconds, bidding_us, acceptance_us),
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

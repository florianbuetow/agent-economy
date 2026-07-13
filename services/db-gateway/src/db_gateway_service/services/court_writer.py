"""Court domain writes (WP-11, B16 god-class decomposition).

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


class CourtWriter:
    """Handles dispute claim, rebuttal, and ruling writes."""

    def __init__(self, writer: DbWriter) -> None:
        self._writer = writer

    def file_claim(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        File a dispute claim.

        INSERT INTO court_claims + INSERT INTO events.
        Idempotency: PK on claim_id.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO court_claims "
                "(claim_id, task_id, claimant_id, respondent_id, reason, status, "
                "rebuttal_deadline, filed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["claim_id"],
                    data["task_id"],
                    data["claimant_id"],
                    data["respondent_id"],
                    data["reason"],
                    data["status"],
                    data.get("rebuttal_deadline"),
                    data["filed_at"],
                ),
            )
            event_id = insert_event(cursor, data["event"])
            db.commit()
            return {"claim_id": data["claim_id"], "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            # court_claims has two UNIQUE-family constraints: claim_id (PK) and
            # task_id (one claim per task). A genuine claim_id collision keeps
            # claim_exists; a task_id collision under a fresh claim_id is a
            # different fact — the task already has a claim filed against it —
            # and reporting it as claim_exists would be misleading (GAP-C7).
            if self._lookup_claim_exists(data["claim_id"]):
                raise ServiceError(
                    "claim_exists",
                    "Claim with this claim_id already exists",
                    409,
                    {},
                ) from exc
            raise ServiceError(
                "task_already_disputed",
                "This task already has a claim filed against it",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def _lookup_claim_exists(self, claim_id: str) -> bool:
        """True if a claim row with this claim_id already exists."""
        cursor = self._writer.connection.execute(
            "SELECT 1 FROM court_claims WHERE claim_id = ?",
            (claim_id,),
        )
        return cursor.fetchone() is not None

    def update_claim_status(
        self,
        claim_id: str,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Update a claim status with optional constraints.

        UPDATE court_claims + INSERT INTO events, in one transaction.
        """
        event = data.get("event")
        if event is None:
            raise ServiceError(
                "missing_field",
                "Missing required field: event",
                400,
                {"field": "event"},
            )
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            if constraints is not None:
                where_clause, where_params = compile_constraints(
                    "court_claims",
                    "claim_id",
                    claim_id,
                    constraints,
                )
                cursor.execute(
                    f"UPDATE court_claims SET status = ? WHERE {where_clause}",  # nosec B608
                    [data["status"], *where_params],
                )
            else:
                cursor.execute(
                    "UPDATE court_claims SET status = ? WHERE claim_id = ?",
                    (data["status"], claim_id),
                )

            if cursor.rowcount == 0:
                if constraints is not None:
                    try:
                        check_constraint_violation(
                            cursor,
                            "court_claims",
                            "claim_id",
                            claim_id,
                            constraints,
                        )
                    except ServiceError:
                        db.rollback()
                        raise
                db.rollback()
                raise ServiceError("claim_not_found", "No claim with this claim_id", 404, {})

            event_id = insert_event(cursor, event)

            db.commit()
            return {
                "claim_id": claim_id,
                "status": data["status"],
                "event_id": event_id,
            }
        except ServiceError:
            raise
        except Exception:
            db.rollback()
            raise

    def submit_rebuttal(
        self,
        data: dict[str, Any],
        constraints: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        Submit a rebuttal with optional claim status update.

        INSERT INTO court_rebuttals + optional UPDATE court_claims + INSERT INTO events.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO court_rebuttals "
                "(rebuttal_id, claim_id, agent_id, content, submitted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data["rebuttal_id"],
                    data["claim_id"],
                    data["agent_id"],
                    data["content"],
                    data["submitted_at"],
                ),
            )
            # Optional claim status update
            claim_status = data.get("claim_status_update")
            if claim_status is not None:
                if constraints is not None:
                    where_clause, where_params = compile_constraints(
                        "court_claims",
                        "claim_id",
                        data["claim_id"],
                        constraints,
                    )
                    cursor.execute(
                        f"UPDATE court_claims SET status = ? WHERE {where_clause}",  # nosec B608
                        [claim_status, *where_params],
                    )
                    if cursor.rowcount == 0:
                        try:
                            check_constraint_violation(
                                cursor,
                                "court_claims",
                                "claim_id",
                                data["claim_id"],
                                constraints,
                            )
                        except ServiceError:
                            db.rollback()
                            raise
                        db.rollback()
                        raise ServiceError(
                            "claim_not_found",
                            "No claim with this claim_id",
                            404,
                            {},
                        )
                else:
                    cursor.execute(
                        "UPDATE court_claims SET status = ? WHERE claim_id = ?",
                        (claim_status, data["claim_id"]),
                    )
            elif constraints is not None:
                verify_cross_table_constraint(
                    cursor,
                    "court_claims",
                    {"claim_id": data["claim_id"], **constraints},
                )
            event_id = insert_event(cursor, data["event"])
            db.commit()
            return {"rebuttal_id": data["rebuttal_id"], "event_id": event_id}
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
                "rebuttal_exists",
                "Rebuttal with this rebuttal_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def record_ruling(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Record a court ruling with optional claim status update.

        INSERT INTO court_rulings + optional UPDATE court_claims + INSERT INTO events.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "INSERT INTO court_rulings "
                "(ruling_id, claim_id, task_id, worker_pct, summary, judge_votes, ruled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    data["ruling_id"],
                    data["claim_id"],
                    data["task_id"],
                    data["worker_pct"],
                    data["summary"],
                    data["judge_votes"],
                    data["ruled_at"],
                ),
            )
            # Optional claim status update
            claim_status = data.get("claim_status_update")
            if claim_status is not None:
                cursor.execute(
                    "UPDATE court_claims SET status = ? WHERE claim_id = ?",
                    (claim_status, data["claim_id"]),
                )
            event_id = insert_event(cursor, data["event"])
            db.commit()
            return {"ruling_id": data["ruling_id"], "event_id": event_id}
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
                "ruling_exists",
                "Ruling with this ruling_id already exists",
                409,
                {},
            ) from exc
        except Exception:
            db.rollback()
            raise

    def delete_ruling(self, claim_id: str, event: dict[str, Any]) -> dict[str, object]:
        """
        Delete a ruling record by claim_id.

        DELETE FROM court_rulings + INSERT INTO events, in one transaction.
        When no ruling matched, nothing was written and no event is logged.
        """
        db = self._writer.connection
        cursor = db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute(
                "DELETE FROM court_rulings WHERE claim_id = ?",
                (claim_id,),
            )
            deleted = cursor.rowcount > 0
            event_id: int | None = None
            if deleted:
                event_id = insert_event(cursor, event)
            db.commit()
            return {"deleted": deleted, "claim_id": claim_id, "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation",
                    "Foreign key constraint failed",
                    409,
                    {},
                ) from exc
            raise
        except Exception:
            db.rollback()
            raise

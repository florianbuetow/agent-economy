"""Court domain writes (WP-11, B16 god-class decomposition).

Extracted from db_writer.py. See identity_writer.py's module docstring for
the shared-connection/no-await invariant this preserves.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from db_gateway_service import task_time
from db_gateway_service.services.db_writer_helpers import (
    check_constraint_violation,
    compile_constraints,
    insert_event,
    is_foreign_key_violation,
    verify_cross_table_constraint,
)
from db_gateway_service.services.task_operation_guards import assert_no_competing_task_work
from db_gateway_service.task_time import parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    from db_gateway_service.services.db_writer import DbWriter

_LEASE_EVENT = "claim.judging_lease"
_RELEASE_EVENT = "claim.judging_released"
_DECISION_EVENT = "ruling.decided"
_ACK_EVENT = "ruling.acknowledged"
_DELIVERED_EVENT = "ruling.delivered"
ACKNOWLEDGMENT_STEPS = frozenset({"task_board", "reputation"})


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
            task_id = self._admit_rebuttal(cursor, data)
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
            cursor.execute(
                "UPDATE board_tasks SET rebuttal_submitted_at=? "
                "WHERE task_id=? AND status='disputed' AND dispute_id=? "
                "AND worker_id=? AND rebuttal_submitted_at IS NULL",
                (data["submitted_at"], task_id, data["claim_id"], data["agent_id"]),
            )
            projected_rows = cursor.rowcount
            if projected_rows != 1:
                raise ServiceError(
                    "rebuttal_projection_conflict", "Task rebuttal projection changed", 409, {}
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
            event = dict(data["event"])
            event.update(
                {
                    "event_source": "court",
                    "event_type": "rebuttal.submitted",
                    "task_id": task_id,
                    "agent_id": data["agent_id"],
                    "timestamp": data["submitted_at"],
                }
            )
            event_id = insert_event(cursor, event)
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

    @staticmethod
    def _admit_rebuttal(cursor: sqlite3.Cursor, data: dict[str, Any]) -> str:
        claim = cursor.execute(
            "SELECT task_id,claimant_id,respondent_id,reason,rebuttal_deadline "
            "FROM court_claims WHERE claim_id=?",
            (data["claim_id"],),
        ).fetchone()
        if claim is None:
            raise ServiceError("claim_not_found", "No claim with this claim_id", 404, {})
        task_id = str(claim[0])
        assert_no_competing_task_work(cursor, task_id)
        task = cursor.execute(
            "SELECT status,dispute_id,poster_id,worker_id,rebuttal_submitted_at,"
            "dispute_reason,rebuttal_deadline "
            "FROM board_tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise ServiceError("payload_mismatch", "Claim task is missing", 409, {})
        if task[0] != "disputed":
            raise ServiceError("invalid_status", "Task is not disputed", 409, {})
        if (task[1], task[2], task[3]) != (data["claim_id"], claim[1], claim[2]):
            raise ServiceError("payload_mismatch", "Claim and task parties do not match", 409, {})
        if (task[5], task[6]) != (claim[3], claim[4]):
            raise ServiceError("payload_mismatch", "Claim and task dispute facts differ", 409, {})
        if data["agent_id"] != task[3]:
            raise ServiceError("forbidden", "Only the task worker can submit a rebuttal", 403, {})
        existing = cursor.execute(
            "SELECT 1 FROM court_rebuttals WHERE claim_id=? LIMIT 1", (data["claim_id"],)
        ).fetchone()
        if existing is not None:
            raise ServiceError(
                "rebuttal_exists", "A rebuttal already exists for this claim", 409, {}
            )
        if task[4] is not None:
            raise ServiceError(
                "rebuttal_projection_conflict", "Task already has a rebuttal observation", 409, {}
            )
        try:
            parse_utc_text_to_epoch_us(data["submitted_at"])
        except ValueError as exc:
            raise ServiceError(
                "invalid_payload",
                "submitted_at must be a representable UTC timestamp",
                400,
                {"field": "submitted_at"},
            ) from exc
        return task_id

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


class CourtRulingWriter:
    """Durable Court ruling progress (plan item 0.3).

    One leased Court run owns a ``judging`` claim at a time. The lease, the
    request snapshot of the decision and every delivery acknowledgment are
    appended to the shared ``events`` log inside the same ``BEGIN IMMEDIATE``
    transaction as the claim/ruling row they guard, so the existing
    version-fingerprinted schema stays unchanged. The latest lease or release
    event for a claim is authoritative; the Gateway clock decides expiry.

    Upgrade path: if these event-derived rows get awkward (volume, querying,
    retention), move lease, request snapshot and acknowledgments into a dedicated
    court_ruling_progress table in the next schema version.
    """

    def __init__(self, writer: DbWriter) -> None:
        self._writer = writer

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _claim_row(cursor: sqlite3.Cursor, claim_id: str) -> tuple[str, str]:
        row = cursor.execute(
            "SELECT task_id, status FROM court_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if row is None:
            raise ServiceError("claim_not_found", "No claim with this claim_id", 404, {})
        return str(row[0]), str(row[1])

    @staticmethod
    def _court_events(
        cursor: sqlite3.Cursor, task_id: str, event_types: tuple[str, ...]
    ) -> list[tuple[str, dict[str, Any]]]:
        # Every caller asks for one or two court event types; a fixed two-slot IN
        # keeps the statement static (the single-type case repeats its type).
        first, second = event_types[0], event_types[-1]
        rows = cursor.execute(
            "SELECT event_type, payload FROM events "
            "WHERE event_source = 'court' AND task_id = ? AND event_type IN (?, ?) "
            "ORDER BY event_id",
            (task_id, first, second),
        ).fetchall()
        events: list[tuple[str, dict[str, Any]]] = []
        for event_type, payload in rows:
            parsed = json.loads(str(payload))
            events.append((str(event_type), parsed if isinstance(parsed, dict) else {}))
        return events

    def _current_lease(
        self, cursor: sqlite3.Cursor, task_id: str, claim_id: str
    ) -> dict[str, Any] | None:
        """Return the live-or-expired lease payload, or None when released/absent."""
        events = [
            (event_type, payload)
            for event_type, payload in self._court_events(
                cursor, task_id, (_LEASE_EVENT, _RELEASE_EVENT)
            )
            if payload.get("claim_id") == claim_id
        ]
        if not events:
            return None
        event_type, payload = events[-1]
        return payload if event_type == _LEASE_EVENT else None

    def _require_lease(
        self, cursor: sqlite3.Cursor, task_id: str, claim_id: str, lease_id: str
    ) -> None:
        lease = self._current_lease(cursor, task_id, claim_id)
        if lease is None or lease.get("lease_id") != lease_id:
            raise ServiceError(
                "judging_lease_lost",
                "This ruling run no longer owns the judging lease",
                409,
                {},
            )

    @staticmethod
    def _ruling_row(cursor: sqlite3.Cursor, claim_id: str) -> tuple[Any, ...] | None:
        row = cursor.execute(
            "SELECT worker_pct, summary, judge_votes, ruled_at FROM court_rulings "
            "WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    def _acknowledged_steps(self, cursor: sqlite3.Cursor, task_id: str, claim_id: str) -> list[str]:
        steps = {
            str(payload.get("step"))
            for _event_type, payload in self._court_events(cursor, task_id, (_ACK_EVENT,))
            if payload.get("claim_id") == claim_id
        }
        return sorted(steps)

    def _decision_request(
        self, cursor: sqlite3.Cursor, task_id: str, claim_id: str
    ) -> dict[str, Any] | None:
        for _event_type, payload in reversed(
            self._court_events(cursor, task_id, (_DECISION_EVENT,))
        ):
            if payload.get("claim_id") == claim_id:
                request = payload.get("request")
                return request if isinstance(request, dict) else None
        return None

    @staticmethod
    def _event(
        cursor: sqlite3.Cursor,
        event_type: str,
        task_id: str,
        summary: str,
        payload: dict[str, Any],
        timestamp: str,
    ) -> int:
        return insert_event(
            cursor,
            {
                "event_source": "court",
                "event_type": event_type,
                "timestamp": timestamp,
                "task_id": task_id,
                "agent_id": None,
                "summary": summary,
                "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            },
        )

    def _transaction(self) -> tuple[sqlite3.Connection, sqlite3.Cursor]:
        db = self._writer.connection
        cursor = db.cursor()
        cursor.execute("BEGIN IMMEDIATE")
        return db, cursor

    # -- commands --------------------------------------------------------

    def acquire_judging_lease(
        self, claim_id: str, lease_id: str, lease_seconds: int
    ) -> dict[str, Any]:
        """CAS ``rebuttal_pending``/expired ``judging`` -> ``judging`` under a new lease."""
        db, cursor = self._transaction()
        try:
            task_id, status = self._claim_row(cursor, claim_id)
            now_us = task_time.utc_now_us()
            reclaimed = False
            if status == "judging":
                lease = self._current_lease(cursor, task_id, claim_id)
                if lease is not None and int(lease["lease_expires_at_us"]) > now_us:
                    raise ServiceError(
                        "judging_lease_held",
                        "Another ruling run holds a live judging lease",
                        409,
                        {},
                    )
                reclaimed = True
            elif status != "rebuttal_pending":
                raise ServiceError(
                    "invalid_claim_status",
                    "Claim cannot be judged from its current status",
                    409,
                    {"current_status": status},
                )
            cursor.execute(
                "UPDATE court_claims SET status = 'judging' WHERE claim_id = ? AND status = ?",
                (claim_id, status),
            )
            if cursor.rowcount != 1:
                raise ServiceError(
                    "invalid_claim_status", "Claim status changed concurrently", 409, {}
                )
            expires_us = task_time.add_seconds_to_epoch_us(now_us, lease_seconds)
            expires_at = task_time.epoch_us_to_utc_text(expires_us)
            self._event(
                cursor,
                _LEASE_EVENT,
                task_id,
                f"Claim {claim_id} judging lease acquired",
                {
                    "claim_id": claim_id,
                    "lease_id": lease_id,
                    "lease_expires_at": expires_at,
                    "lease_expires_at_us": expires_us,
                    "reclaimed": reclaimed,
                },
                task_time.epoch_us_to_utc_text(now_us),
            )
            decided = self._ruling_row(cursor, claim_id) is not None
            db.commit()
            return {
                "claim_id": claim_id,
                "status": "judging",
                "lease_id": lease_id,
                "lease_expires_at": expires_at,
                "reclaimed": reclaimed,
                "decided": decided,
            }
        except Exception:
            db.rollback()
            raise

    def release_judging_lease(self, claim_id: str, lease_id: str) -> dict[str, Any]:
        """Give up the lease; an undecided claim returns to ``rebuttal_pending``."""
        db, cursor = self._transaction()
        try:
            task_id, status = self._claim_row(cursor, claim_id)
            if status != "judging":
                raise ServiceError("judging_lease_lost", "Claim is no longer being judged", 409, {})
            self._require_lease(cursor, task_id, claim_id, lease_id)
            decided = self._ruling_row(cursor, claim_id) is not None
            new_status = "judging" if decided else "rebuttal_pending"
            if not decided:
                cursor.execute(
                    "UPDATE court_claims SET status = 'rebuttal_pending' "
                    "WHERE claim_id = ? AND status = 'judging'",
                    (claim_id,),
                )
            self._event(
                cursor,
                _RELEASE_EVENT,
                task_id,
                f"Claim {claim_id} judging lease released",
                {"claim_id": claim_id, "lease_id": lease_id},
                task_time.epoch_us_to_utc_text(task_time.utc_now_us()),
            )
            db.commit()
            return {"claim_id": claim_id, "status": new_status, "decided": decided}
        except Exception:
            db.rollback()
            raise

    def claim_decision(self, claim_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Persist the judged decision and its delivery request snapshot exactly once."""
        db, cursor = self._transaction()
        try:
            task_id, status = self._claim_row(cursor, claim_id)
            if status != "judging":
                raise ServiceError("judging_lease_lost", "Claim is no longer being judged", 409, {})
            self._require_lease(cursor, task_id, claim_id, str(data["lease_id"]))
            if self._ruling_row(cursor, claim_id) is not None:
                raise ServiceError(
                    "ruling_exists", "A decision is already recorded for this claim", 409, {}
                )
            decided_at = task_time.epoch_us_to_utc_text(task_time.utc_now_us())
            cursor.execute(
                "INSERT INTO court_rulings "
                "(ruling_id, claim_id, task_id, worker_pct, summary, judge_votes, ruled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    claim_id,
                    claim_id,
                    task_id,
                    data["worker_pct"],
                    data["summary"],
                    data["judge_votes"],
                    decided_at,
                ),
            )
            event_id = self._event(
                cursor,
                _DECISION_EVENT,
                task_id,
                f"Ruling decided for claim {claim_id}",
                {
                    "claim_id": claim_id,
                    "lease_id": data["lease_id"],
                    "worker_pct": data["worker_pct"],
                    "request": data["request"],
                },
                decided_at,
            )
            db.commit()
            return {"ruling_id": claim_id, "decided_at": decided_at, "event_id": event_id}
        except sqlite3.IntegrityError as exc:
            db.rollback()
            if is_foreign_key_violation(exc):
                raise ServiceError(
                    "foreign_key_violation", "Foreign key constraint failed", 409, {}
                ) from exc
            raise
        except Exception:
            db.rollback()
            raise

    def record_acknowledgment(self, claim_id: str, lease_id: str, step: str) -> dict[str, Any]:
        """Record one completed delivery of the stored decision (idempotent)."""
        db, cursor = self._transaction()
        try:
            task_id, status = self._claim_row(cursor, claim_id)
            if status != "judging":
                raise ServiceError("judging_lease_lost", "Claim is no longer being judged", 409, {})
            self._require_lease(cursor, task_id, claim_id, lease_id)
            if self._ruling_row(cursor, claim_id) is None:
                raise ServiceError(
                    "decision_missing", "No decision is recorded for this claim", 409, {}
                )
            if step not in self._acknowledged_steps(cursor, task_id, claim_id):
                self._event(
                    cursor,
                    _ACK_EVENT,
                    task_id,
                    f"Ruling for claim {claim_id} acknowledged by {step}",
                    {"claim_id": claim_id, "lease_id": lease_id, "step": step},
                    task_time.epoch_us_to_utc_text(task_time.utc_now_us()),
                )
            acknowledged = self._acknowledged_steps(cursor, task_id, claim_id)
            db.commit()
            return {"claim_id": claim_id, "acknowledgments": acknowledged}
        except Exception:
            db.rollback()
            raise

    def complete_ruling(self, claim_id: str, lease_id: str) -> dict[str, Any]:
        """CAS ``judging`` -> ``ruled`` once every delivery is acknowledged."""
        db, cursor = self._transaction()
        try:
            task_id, status = self._claim_row(cursor, claim_id)
            if status == "ruled":
                db.rollback()
                return {"claim_id": claim_id, "status": "ruled"}
            if status != "judging":
                raise ServiceError("judging_lease_lost", "Claim is no longer being judged", 409, {})
            self._require_lease(cursor, task_id, claim_id, lease_id)
            ruling = self._ruling_row(cursor, claim_id)
            if ruling is None:
                raise ServiceError(
                    "decision_missing", "No decision is recorded for this claim", 409, {}
                )
            missing = ACKNOWLEDGMENT_STEPS - set(
                self._acknowledged_steps(cursor, task_id, claim_id)
            )
            if missing:
                raise ServiceError(
                    "acknowledgments_pending",
                    "Ruling deliveries are not all acknowledged",
                    409,
                    {"missing": sorted(missing)},
                )
            cursor.execute(
                "UPDATE court_claims SET status = 'ruled' "
                "WHERE claim_id = ? AND status = 'judging'",
                (claim_id,),
            )
            self._event(
                cursor,
                _DELIVERED_EVENT,
                task_id,
                f"Ruling delivered for claim {claim_id}",
                {"claim_id": claim_id, "worker_pct": ruling[0]},
                task_time.epoch_us_to_utc_text(task_time.utc_now_us()),
            )
            db.commit()
            return {"claim_id": claim_id, "status": "ruled"}
        except Exception:
            db.rollback()
            raise

    def ruling_progress(self, claim_id: str) -> dict[str, Any]:
        """Return the stored decision, its request snapshot and acknowledgments."""
        cursor = self._writer.connection.cursor()
        task_id, status = self._claim_row(cursor, claim_id)
        ruling = self._ruling_row(cursor, claim_id)
        decision: dict[str, Any] | None = None
        if ruling is not None:
            decision = {
                "worker_pct": ruling[0],
                "summary": ruling[1],
                "judge_votes": ruling[2],
                "decided_at": ruling[3],
                "request": self._decision_request(cursor, task_id, claim_id),
            }
        return {
            "claim_id": claim_id,
            "status": status,
            "decision": decision,
            "acknowledgments": self._acknowledged_steps(cursor, task_id, claim_id),
        }

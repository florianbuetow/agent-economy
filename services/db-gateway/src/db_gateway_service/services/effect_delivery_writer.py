"""Durable scheduling and atomic receipt adoption for MP-08 financial effects."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final

from service_commons.exceptions import ServiceError

from db_gateway_service.effect_delivery_transitions import (
    ApplyTransition,
    DeliveryQueuePolicy,
    DeliveryQueueState,
    EvidenceRequired,
    RejectTransition,
    plan_dead_letter_evidence_absent,
    plan_evidence_absent,
    plan_expiry,
    plan_failure,
    plan_lease,
    plan_operator_retry,
)
from db_gateway_service.financial_effect_projection import (
    NativeFinancialEvidence,
    build_legacy_financial_intent,
    evaluate_financial_receipt,
    evaluate_native_financial_evidence,
)
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.services.task_operation_writer import TaskOperationWriter, _json_object

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

    from db_gateway_service.config import BoardEffectRecoveryConfig

_MAX_EPOCH_US: Final[int] = 253_402_300_799_999_999
_DELIVERY_COLUMNS: Final[str] = (
    "effect_operation_id,recovery_contract_version,task_id,source_kind,"
    "native_operation_id,legacy_observation_id,owner_caller_kid,effect_kind,"
    "delivery_state,enqueued_at_us,total_attempt_count,cycle_attempt_count,"
    "lease_generation,next_attempt_at_us,lease_owner_id,lease_acquired_at_us,"
    "lease_expires_at_us,last_failure_code,last_failure_at_us,operator_retry_count,"
    "last_operator_retry_at_us,last_operator_retry_caller_kid,next_evidence_check_at_us,"
    "completed_at_us,completion_event_id,cutover_singleton"
)
_DELIVERY_SELECT: Final[str] = f"SELECT {_DELIVERY_COLUMNS} FROM board_task_effect_deliveries"  # nosec B608
_DELIVERY_DUE_SELECT: Final[str] = (
    f"SELECT {_DELIVERY_COLUMNS},"  # nosec B608
    "CASE delivery_state WHEN 'pending' THEN next_attempt_at_us "
    "ELSE lease_expires_at_us END AS effective_due "
    "FROM board_task_effect_deliveries"
)


def _error(error: str, message: str, status: int, details: dict[str, object]) -> ServiceError:
    return ServiceError(error, message, status, details)


class EffectDeliveryWriter:
    """Own recovery queue transactions on the Gateway's single connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        clock_us: Callable[[], int],
        current_caller_kid: str,
        deadline_actor_id: str | None,
        platform_public_key: str | None,
    ) -> None:
        if not current_caller_kid:
            raise ValueError("current_caller_kid must be a nonempty string")
        self._db = connection
        self._clock_us = clock_us
        self._current_caller_kid = current_caller_kid
        self._task_writer = (
            None
            if deadline_actor_id is None or platform_public_key is None
            else TaskOperationWriter(
                connection,
                clock_us,
                deadline_actor_id,
                platform_public_key,
                current_task_board_caller_kid=current_caller_kid,
            )
        )

    def get(self, operation_id: str, caller_kid: str) -> dict[str, object]:
        """Return one owner-filtered delivery diagnostic."""
        row = self._owned_delivery(self._db.cursor(), operation_id, caller_kid)
        now = self._sample_clock()
        return self._diagnostic(self._db.cursor(), row, now)

    def list(
        self,
        *,
        state: str,
        after: str,
        limit: int,
        caller_kid: str,
    ) -> dict[str, object]:
        """Return a bounded owner-filtered lexical page."""
        self._owner(caller_kid)
        now = self._sample_clock()
        clause = "" if state == "all" else "AND delivery_state=? "
        params: list[object] = [caller_kid]
        if state != "all":
            params.append(state)
        params.extend((after, limit + 1))
        rows = self._db.execute(
            f"SELECT {_DELIVERY_COLUMNS} FROM board_task_effect_deliveries "  # nosec B608
            "WHERE owner_caller_kid=? "
            f"{clause}AND effect_operation_id>? COLLATE BINARY "
            "ORDER BY effect_operation_id COLLATE BINARY LIMIT ?",
            params,
        ).fetchall()
        page = rows[:limit]
        next_after = str(page[-1][0]) if len(rows) > limit and page else None
        cursor = self._db.cursor()
        return {
            "protocol_version": 1,
            "items": [self._diagnostic(cursor, tuple(row), now) for row in page],
            "next_after": next_after,
        }

    def lease(
        self,
        worker_id: str,
        caller_kid: str,
        settings: BoardEffectRecoveryConfig,
    ) -> dict[str, object]:
        """Inspect one due dead letter, normalize due work, and lease one item."""
        owner = self._owner(caller_kid)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            now = self._sample_clock()
            due_dead = cursor.execute(
                _DELIVERY_SELECT + " WHERE owner_caller_kid=? AND delivery_state='dead_letter' "
                "AND next_evidence_check_at_us<=? ORDER BY next_evidence_check_at_us,"
                "effect_operation_id COLLATE BINARY LIMIT 1",
                (owner, now),
            ).fetchone()
            if due_dead is not None:
                dead_row = tuple(due_dead)
                outcome = self._adopt(cursor, dead_row, now)
                if outcome == "absent":
                    planned = plan_dead_letter_evidence_absent(
                        self._queue_state(dead_row),
                        self._queue_policy(settings),
                        now,
                    )
                    assert isinstance(planned, (ApplyTransition, RejectTransition))
                    self._apply_or_raise(cursor, planned, str(dead_row[0]))

            leased = self._lease_due(cursor, owner, worker_id, now, settings)
            self._db.commit()
            return {
                "protocol_version": 1,
                "delivery": None if leased is None else self._lease_envelope(cursor, leased),
            }
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise

    def fail(
        self,
        operation_id: str,
        generation: int,
        worker_id: str,
        failure_code: str,
        caller_kid: str,
        settings: BoardEffectRecoveryConfig,
    ) -> dict[str, object]:
        """Record one exact active attempt's bounded failure result."""
        cursor = self._db.cursor()
        conflict = False
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = self._owned_delivery(cursor, operation_id, caller_kid)
            if (
                str(row[8]) != "leased"
                or str(row[14]) != worker_id
                or int(row[12]) != generation
                or type(row[16]) is not int
            ):
                raise _error(
                    "effect_delivery_attempt_stale",
                    "The recovery attempt fence is stale",
                    409,
                    {"effect_operation_id": operation_id},
                )
            now = self._sample_clock()
            state = self._queue_state(row)
            policy = self._queue_policy(settings)
            plan = plan_failure(
                state,
                policy,
                now,
                worker_id,
                generation,
                failure_code,
            )
            if isinstance(plan, RejectTransition) and plan.reason == "stale":
                raise _error(
                    "effect_delivery_attempt_stale",
                    "The recovery attempt fence is stale",
                    409,
                    {"effect_operation_id": operation_id},
                )
            if isinstance(plan, EvidenceRequired):
                outcome = self._adopt(cursor, row, now)
                if outcome == "absent":
                    absent = plan_evidence_absent(state, policy, plan)
                    self._apply_or_raise(cursor, absent, operation_id)
                conflict = outcome == "blocked"
            else:
                assert isinstance(plan, (ApplyTransition, RejectTransition))
                self._apply_or_raise(cursor, plan, operation_id)
            refreshed = self._delivery(cursor, operation_id)
            assert refreshed is not None
            diagnostic = self._diagnostic(cursor, refreshed, now)
            self._db.commit()
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise
        if conflict:
            raise _error(
                "effect_evidence_conflict",
                "Persisted financial evidence conflicts with the intended effect",
                409,
                {"effect_operation_id": operation_id},
            )
        return {"protocol_version": 1, "delivery": diagnostic}

    def retry(
        self,
        operation_id: str,
        caller_kid: str,
        settings: BoardEffectRecoveryConfig,
    ) -> dict[str, object]:
        """Requeue one same-owner dead letter without resetting lifetime counters."""
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = self._owned_delivery(cursor, operation_id, caller_kid)
            now = self._sample_clock()
            plan = plan_operator_retry(
                self._queue_state(row),
                self._queue_policy(settings),
                now,
                caller_kid,
            )
            if isinstance(plan, RejectTransition) and plan.reason == "not_retryable":
                raise _error(
                    "effect_delivery_not_retryable",
                    "Only dead-letter deliveries can be retried",
                    409,
                    {"effect_operation_id": operation_id},
                )
            if isinstance(plan, RejectTransition) and plan.reason == "counter_exhausted":
                raise _error(
                    "effect_delivery_counter_exhausted",
                    "A recovery counter cannot be incremented",
                    409,
                    {"effect_operation_id": operation_id},
                )
            assert isinstance(plan, (ApplyTransition, RejectTransition))
            self._apply_or_raise(cursor, plan, operation_id)
            refreshed = self._delivery(cursor, operation_id)
            assert refreshed is not None
            result: dict[str, object] = {
                "protocol_version": 1,
                "delivery": self._diagnostic(cursor, refreshed, now),
            }
            self._db.commit()
            return result
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise

    def finalize_native(self, operation_id: str, caller_kid: str) -> dict[str, object]:
        """Adopt one native receipt and its business projection atomically."""
        cursor = self._db.cursor()
        conflict = False
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = self._owned_delivery(cursor, operation_id, caller_kid)
            if str(row[3]) != "native_task_operation":
                raise _error(
                    "effect_delivery_source_not_native",
                    "This delivery is not backed by a native task operation",
                    400,
                    {"effect_operation_id": operation_id},
                )
            now = self._sample_clock()
            before_state = str(row[8])
            outcome = self._adopt(cursor, row, now)
            if outcome == "absent":
                diagnostic = self._diagnostic(cursor, row, now)
                self._db.rollback()
                return {"protocol_version": 1, "delivery": diagnostic}
            conflict = outcome == "blocked"
            refreshed = self._delivery(cursor, operation_id)
            assert refreshed is not None
            diagnostic = self._diagnostic(cursor, refreshed, now)
            self._db.commit()
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise
        if conflict:
            raise _error(
                "effect_evidence_conflict",
                "Persisted financial evidence conflicts with the intended effect",
                409,
                {"effect_operation_id": operation_id},
            )
        assert before_state in {"pending", "leased", "dead_letter", "completed"}
        return {"protocol_version": 1, "delivery": diagnostic}

    def finalize_legacy(self, observation_id: str, caller_kid: str) -> dict[str, object]:
        """Finalize one supported migrated settlement observation without dispatch."""
        cursor = self._db.cursor()
        conflict = False
        try:
            cursor.execute("BEGIN IMMEDIATE")
            found = cursor.execute(
                _DELIVERY_SELECT + " WHERE legacy_observation_id=? AND owner_caller_kid=?",
                (observation_id, self._owner(caller_kid)),
            ).fetchone()
            if found is None:
                raise _error(
                    "effect_delivery_not_found",
                    "No caller-owned effect delivery was found",
                    404,
                    {},
                )
            row = tuple(found)
            now = self._sample_clock()
            outcome = self._adopt(cursor, row, now)
            if outcome == "absent":
                source = cursor.execute(
                    "SELECT observation_kind FROM board_legacy_effect_observations "
                    "WHERE observation_id=?",
                    (observation_id,),
                ).fetchone()
                if source is not None and str(source[0]) == "worker_settlement_pending":
                    diagnostic = self._diagnostic(cursor, row, now)
                    self._db.rollback()
                    return {"protocol_version": 1, "delivery": diagnostic}
                self._block_legacy(cursor, row, now)
                conflict = True
            else:
                conflict = outcome == "blocked"
            refreshed = self._delivery(cursor, str(row[0]))
            assert refreshed is not None
            diagnostic = self._diagnostic(cursor, refreshed, now)
            self._db.commit()
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise
        if conflict:
            raise _error(
                "effect_evidence_conflict",
                "Persisted financial evidence conflicts with the intended effect",
                409,
                {"observation_id": observation_id},
            )
        return {"protocol_version": 1, "delivery": diagnostic}

    def _lease_due(
        self,
        cursor: sqlite3.Cursor,
        owner: str,
        worker_id: str,
        now: int,
        settings: BoardEffectRecoveryConfig,
    ) -> tuple[Any, ...] | None:
        while True:
            found = cursor.execute(
                _DELIVERY_DUE_SELECT + " WHERE owner_caller_kid=? "
                "AND ((delivery_state='pending' AND next_attempt_at_us<=?) "
                "OR (delivery_state='leased' AND lease_expires_at_us<=?)) "
                "ORDER BY effective_due,effect_operation_id COLLATE BINARY LIMIT 1",
                (owner, now, now),
            ).fetchone()
            if found is None:
                return None
            row = tuple(found[:-1])
            state = self._queue_state(row)
            policy = self._queue_policy(settings)
            plan = (
                plan_expiry(state, policy, now)
                if state.delivery_state == "leased"
                else plan_lease(state, policy, now, worker_id)
            )
            if isinstance(plan, EvidenceRequired):
                outcome = self._adopt(cursor, row, now)
                if outcome == "absent":
                    absent = plan_evidence_absent(state, policy, plan)
                    self._apply_or_raise(cursor, absent, str(row[0]))
                continue
            assert isinstance(plan, (ApplyTransition, RejectTransition))
            applied = self._apply_or_raise(cursor, plan, str(row[0]))
            if applied.delivery_state != "leased":
                continue
            refreshed = self._delivery(cursor, str(row[0]))
            assert refreshed is not None
            return refreshed

    def _adopt(self, cursor: sqlite3.Cursor, row: tuple[Any, ...], now: int) -> str:
        if str(row[8]) == "completed":
            return "completed"
        if str(row[3]) == "native_task_operation":
            return self._adopt_native(cursor, row, now)
        return self._adopt_legacy(cursor, row, now)

    def _adopt_native(self, cursor: sqlite3.Cursor, row: tuple[Any, ...], now: int) -> str:
        task_writer = self._task_writer
        if task_writer is None:
            raise _error(
                "service_not_ready",
                "Task operation authority is required for native recovery",
                503,
                {},
            )
        operation = task_writer._operation_row(cursor, str(row[4]))
        if operation is None:
            self._block_native(cursor, row, None, now)
            result = "blocked"
        else:
            task = task_writer._task(cursor, str(operation[2]))
            contract = task_writer._task_contract(cursor, str(operation[2]))
            evidence, winner = task_writer._financial_evidence(cursor, operation, task, contract)
            native = evaluate_native_financial_evidence(cursor.connection, str(row[0]))
            result = self._apply_native_evidence(
                cursor,
                row,
                now,
                operation,
                task,
                contract,
                evidence,
                winner,
                native,
                task_writer,
            )
        return result

    def _apply_native_evidence(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        now: int,
        operation: tuple[Any, ...],
        task: dict[str, Any],
        contract: dict[str, Any],
        evidence: bool | None,
        winner: str | None,
        native: NativeFinancialEvidence,
        task_writer: TaskOperationWriter,
    ) -> str:
        if evidence is None and native.receipt.state == "absent":
            return "absent"
        receipt = cursor.execute(
            "SELECT event_id FROM bank_operation_receipts WHERE operation_id=?",
            (str(row[0]),),
        ).fetchone()
        source_state = str(operation[14])
        if (
            evidence is not True
            or native.receipt.state == "conflict"
            or not native.source_pair_valid
            or receipt is None
            or source_state == "blocked"
        ):
            self._block_native(cursor, row, winner, now)
            return "blocked"
        if source_state == "claimed":
            if native.projection_state != "source_current":
                self._block_native(cursor, row, winner, now)
                return "blocked"
            try:
                task_writer._validate_claim_ownership(operation, task, contract)
            except ServiceError:
                self._block_native(cursor, row, winner, now)
                return "blocked"
            task_writer._complete_bank_effect(cursor, operation, task, contract, now)
        elif source_state != "completed" or native.projection_state != "target_current":
            self._block_native(cursor, row, winner, now)
            return "blocked"
        self._complete_delivery(cursor, str(row[0]), now, int(receipt[0]))
        return "completed"

    def _adopt_legacy(self, cursor: sqlite3.Cursor, row: tuple[Any, ...], now: int) -> str:
        observation = cursor.execute(
            "SELECT observation_kind,observed_fact_json,observed_fact_sha256,"
            "external_reference,unresolved,task_id FROM board_legacy_effect_observations "
            "WHERE observation_id=?",
            (str(row[5]),),
        ).fetchone()
        task_row = cursor.execute("SELECT * FROM board_tasks WHERE task_id=?", (row[2],)).fetchone()
        if observation is None or task_row is None:
            self._block_legacy(cursor, row, now)
            result = "blocked"
        else:
            task = dict(task_row)
            intent = build_legacy_financial_intent(tuple(observation[:5]), task)
            if intent is None:
                self._block_legacy(cursor, row, now)
                result = "blocked"
            else:
                result = self._apply_legacy_evidence(
                    cursor,
                    row,
                    now,
                    tuple(observation),
                    task,
                    evaluate_financial_receipt(cursor.connection, str(row[0]), intent).state,
                )
        return result

    def _apply_legacy_evidence(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        now: int,
        observation: tuple[Any, ...],
        task: dict[str, Any],
        evidence_state: str,
    ) -> str:
        if evidence_state == "absent":
            return "absent"
        if evidence_state == "conflict":
            self._block_legacy(cursor, row, now)
            return "blocked"
        kind = str(observation[0])
        status = str(task["status"])
        source_valid = (kind == "worker_settlement_pending" and status == "approved") or (
            kind == "worker_settlement_succeeded" and status in {"submitted", "approved"}
        )
        if not source_valid:
            self._block_legacy(cursor, row, now)
            return "blocked"
        if kind == "worker_settlement_pending":
            cursor.execute(
                "UPDATE board_tasks SET escrow_pending=0 WHERE task_id=? AND status='approved'",
                (row[2],),
            )
        else:
            if kind != "worker_settlement_succeeded":
                self._block_legacy(cursor, row, now)
                return "blocked"
            cursor.execute(
                "UPDATE board_tasks SET status='approved',escrow_pending=0 WHERE task_id=?",
                (row[2],),
            )
        cursor.execute(
            "UPDATE board_legacy_effect_observations SET unresolved=0 WHERE observation_id=?",
            (row[5],),
        )
        receipt = cursor.execute(
            "SELECT event_id FROM bank_operation_receipts WHERE operation_id=?",
            (row[0],),
        ).fetchone()
        assert receipt is not None
        self._complete_delivery(cursor, str(row[0]), now, int(receipt[0]))
        return "completed"

    def _block_native(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        winner: str | None,
        now: int,
    ) -> None:
        details: dict[str, object] = {"operation_id": str(row[0])}
        if winner is not None:
            details["winning_operation_id"] = winner
        error = {
            "error": "effect_evidence_conflict",
            "message": "Persisted effect evidence contradicts the claimed task operation",
            "details": details,
        }
        cursor.execute(
            "UPDATE board_task_operations SET operation_state='blocked',effect_state='conflict',"
            "error_json=? WHERE operation_id=? AND operation_state!='completed'",
            (_json_object(error), row[4]),
        )
        self._block_delivery(cursor, str(row[0]), now)

    def _block_legacy(self, cursor: sqlite3.Cursor, row: tuple[Any, ...], now: int) -> None:
        self._block_delivery(cursor, str(row[0]), now)

    @staticmethod
    def _block_delivery(cursor: sqlite3.Cursor, operation_id: str, now: int) -> None:
        cursor.execute(
            "UPDATE board_task_effect_deliveries SET delivery_state='integrity_blocked',"
            "next_attempt_at_us=NULL,lease_owner_id=NULL,lease_acquired_at_us=NULL,"
            "lease_expires_at_us=NULL,last_failure_code='effect_evidence_conflict',"
            "last_failure_at_us=?,next_evidence_check_at_us=NULL,completed_at_us=NULL,"
            "completion_event_id=NULL WHERE effect_operation_id=?",
            (now, operation_id),
        )

    @staticmethod
    def _complete_delivery(
        cursor: sqlite3.Cursor,
        operation_id: str,
        now: int,
        receipt_event_id: int,
    ) -> None:
        cursor.execute(
            "UPDATE board_task_effect_deliveries SET delivery_state='completed',"
            "next_attempt_at_us=NULL,lease_owner_id=NULL,lease_acquired_at_us=NULL,"
            "lease_expires_at_us=NULL,next_evidence_check_at_us=NULL,completed_at_us=?,"
            "completion_event_id=? WHERE effect_operation_id=?",
            (now, receipt_event_id, operation_id),
        )

    def _diagnostic(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        observed_at_us: int,
    ) -> dict[str, object]:
        evidence = self._evidence_for_row(cursor, row)
        return {
            "effect_operation_id": row[0],
            "task_id": row[2],
            "source_kind": row[3],
            "source_id": row[4] if row[3] == "native_task_operation" else row[5],
            "effect_kind": row[7],
            "delivery_state": row[8],
            "enqueued_at_us": row[9],
            "observed_at_us": observed_at_us,
            "total_attempt_count": row[10],
            "cycle_attempt_count": row[11],
            "lease_generation": row[12],
            "next_attempt_at_us": row[13],
            "lease_owner_id": row[14],
            "lease_acquired_at_us": row[15],
            "lease_expires_at_us": row[16],
            "last_failure_code": row[17],
            "last_failure_at_us": row[18],
            "operator_retry_count": row[19],
            "last_operator_retry_at_us": row[20],
            "next_evidence_check_at_us": row[22],
            "completed_at_us": row[23],
            "completion_event_id": row[24],
            "receipt_reference": str(row[0]) if evidence == "confirmed" else None,
        }

    def _evidence_for_row(self, cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> str:
        if row[3] == "native_task_operation":
            operation = cursor.execute(
                "SELECT effect_intent_json FROM board_task_operations WHERE operation_id=?",
                (row[4],),
            ).fetchone()
            if operation is None or operation[0] is None:
                return "conflict"
            try:
                intent = json.loads(str(operation[0]))
            except (TypeError, ValueError):
                return "conflict"
        else:
            observation = cursor.execute(
                "SELECT observation_kind,observed_fact_json,observed_fact_sha256,"
                "external_reference,unresolved FROM board_legacy_effect_observations "
                "WHERE observation_id=?",
                (row[5],),
            ).fetchone()
            task = cursor.execute("SELECT * FROM board_tasks WHERE task_id=?", (row[2],)).fetchone()
            if observation is None or task is None:
                return "conflict"
            intent = build_legacy_financial_intent(tuple(observation), dict(task))
            if intent is None:
                return "conflict"
        return evaluate_financial_receipt(cursor.connection, str(row[0]), intent).state

    def _lease_envelope(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
    ) -> dict[str, object]:
        if row[3] == "native_task_operation":
            source = cursor.execute(
                "SELECT effect_intent_json FROM board_task_operations WHERE operation_id=?",
                (row[4],),
            ).fetchone()
            if source is None or source[0] is None:
                raise _error("effect_delivery_source_invalid", "Effect intent is missing", 500, {})
            stored = json.loads(str(source[0]))
        else:
            observation = cursor.execute(
                "SELECT observation_kind,observed_fact_json,observed_fact_sha256,"
                "external_reference,unresolved FROM board_legacy_effect_observations "
                "WHERE observation_id=?",
                (row[5],),
            ).fetchone()
            task = cursor.execute("SELECT * FROM board_tasks WHERE task_id=?", (row[2],)).fetchone()
            if observation is None or task is None:
                raise _error("effect_delivery_source_invalid", "Legacy source is missing", 500, {})
            stored = build_legacy_financial_intent(tuple(observation), dict(task))
            if stored is None:
                raise _error("effect_delivery_source_invalid", "Legacy source is invalid", 500, {})
        effect_intent = {
            "financial_request": stored["financial_request"],
            "request_sha256": stored.get("financial_request_sha256", stored.get("request_sha256")),
            "expected_escrow": stored["expected_escrow"],
        }
        return {
            "effect_operation_id": row[0],
            "task_id": row[2],
            "source_kind": row[3],
            "source_id": row[4] if row[3] == "native_task_operation" else row[5],
            "effect_kind": row[7],
            "effect_intent": effect_intent,
            "worker_id": row[14],
            "lease_generation": row[12],
            "total_attempt_count": row[10],
            "cycle_attempt_count": row[11],
            "lease_acquired_at_us": row[15],
            "lease_expires_at_us": row[16],
        }

    def _owned_delivery(
        self,
        cursor: sqlite3.Cursor,
        operation_id: str,
        caller_kid: str,
    ) -> tuple[Any, ...]:
        owner = self._owner(caller_kid)
        row = cursor.execute(
            _DELIVERY_SELECT + " WHERE effect_operation_id=? AND owner_caller_kid=?",
            (operation_id, owner),
        ).fetchone()
        if row is None:
            raise _error(
                "effect_delivery_not_found",
                "No caller-owned effect delivery was found",
                404,
                {},
            )
        return tuple(row)

    @staticmethod
    def _delivery(cursor: sqlite3.Cursor, operation_id: str) -> tuple[Any, ...] | None:
        row = cursor.execute(
            _DELIVERY_SELECT + " WHERE effect_operation_id=?",
            (operation_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    def _owner(self, caller_kid: str) -> str:
        if caller_kid != self._current_caller_kid:
            return caller_kid
        return caller_kid

    def _sample_clock(self) -> int:
        value = self._clock_us()
        if type(value) is not int or not 0 <= value <= _MAX_EPOCH_US:
            raise _error(
                "effect_delivery_clock_exhausted",
                "Gateway recovery clock is outside the supported epoch range",
                503,
                {},
            )
        return value

    @staticmethod
    def _queue_policy(settings: BoardEffectRecoveryConfig) -> DeliveryQueuePolicy:
        return DeliveryQueuePolicy(
            max_attempts_per_delivery=settings.max_attempts_per_delivery,
            counter_max=SQLITE_MAX_INTEGER,
            timestamp_max_us=_MAX_EPOCH_US,
            initial_backoff_us=settings.initial_backoff_seconds * 1_000_000,
            max_backoff_us=settings.max_backoff_seconds * 1_000_000,
            lease_duration_us=settings.lease_duration_seconds * 1_000_000,
            evidence_interval_us=settings.evidence_interval_seconds * 1_000_000,
        )

    @staticmethod
    def _queue_state(row: tuple[Any, ...]) -> DeliveryQueueState:
        return DeliveryQueueState(
            item_id=str(row[0]),
            delivery_state=str(row[8]),
            total_attempt_count=int(row[10]),
            cycle_attempt_count=int(row[11]),
            lease_generation=int(row[12]),
            next_attempt_at_us=row[13],
            lease_owner_id=row[14],
            lease_acquired_at_us=row[15],
            lease_expires_at_us=row[16],
            last_failure_code=row[17],
            last_failure_at_us=row[18],
            operator_retry_count=int(row[19]),
            last_operator_retry_at_us=row[20],
            last_operator_retry_caller_id=row[21],
            next_evidence_check_at_us=row[22],
        )

    @staticmethod
    def _persist_queue_state(cursor: sqlite3.Cursor, state: DeliveryQueueState) -> None:
        cursor.execute(
            "UPDATE board_task_effect_deliveries SET delivery_state=?,"
            "total_attempt_count=?,cycle_attempt_count=?,lease_generation=?,"
            "next_attempt_at_us=?,lease_owner_id=?,lease_acquired_at_us=?,"
            "lease_expires_at_us=?,last_failure_code=?,last_failure_at_us=?,"
            "operator_retry_count=?,last_operator_retry_at_us=?,"
            "last_operator_retry_caller_kid=?,next_evidence_check_at_us=? "
            "WHERE effect_operation_id=?",
            (
                state.delivery_state,
                state.total_attempt_count,
                state.cycle_attempt_count,
                state.lease_generation,
                state.next_attempt_at_us,
                state.lease_owner_id,
                state.lease_acquired_at_us,
                state.lease_expires_at_us,
                state.last_failure_code,
                state.last_failure_at_us,
                state.operator_retry_count,
                state.last_operator_retry_at_us,
                state.last_operator_retry_caller_id,
                state.next_evidence_check_at_us,
                state.item_id,
            ),
        )

    def _apply_or_raise(
        self,
        cursor: sqlite3.Cursor,
        plan: ApplyTransition | RejectTransition,
        operation_id: str,
    ) -> DeliveryQueueState:
        if isinstance(plan, ApplyTransition):
            self._persist_queue_state(cursor, plan.state)
            return plan.state
        if plan.reason == "clock_exhausted":
            raise _error(
                "effect_delivery_clock_exhausted",
                "Gateway recovery timestamp exceeds the supported epoch range",
                503,
                {},
            )
        raise _error(
            "effect_delivery_state_conflict",
            "Delivery queue transition is not applicable",
            409,
            {"effect_operation_id": operation_id, "reason": plan.reason},
        )


__all__ = ["EffectDeliveryWriter"]

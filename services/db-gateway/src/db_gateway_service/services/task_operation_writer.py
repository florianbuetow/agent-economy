"""Atomic native Task Board commands and receipt-backed finalization."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any, Final

from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.services.artifact_acceptance import (
    AcceptedArtifactBinding,
    EmptyInputAcceptanceBinding,
    commit_acceptance_binding,
    inspect_acceptance_binding,
)
from db_gateway_service.services.artifact_submission import (
    FrozenSubmissionManifest,
    freeze_submission_manifest,
    reject_governed_protocol_v1_submission,
)
from db_gateway_service.services.db_writer_helpers import (
    is_foreign_key_violation,
    is_unique_violation,
)
from db_gateway_service.services.task_operation_court import (
    CourtClaimEvidence,
    build_court_claim_intent,
    configured_platform_actor,
    evaluate_court_claim,
    locked_escrow_matches,
)
from db_gateway_service.services.task_operation_guards import assert_no_competing_task_work
from db_gateway_service.settlement_terms import (
    settlement_request_fields,
    worker_amount_for,
)
from db_gateway_service.task_operation_requests import (
    COMMAND_TARGET_STATUS,
    TaskOperationRequest,
    parse_operation_id,
)
from db_gateway_service.task_time import add_seconds_to_epoch_us, epoch_us_to_utc_text

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_BANK_COMMANDS: Final[frozenset[str]] = frozenset(
    {
        "cancel_task",
        "expire_bidding",
        "expire_acceptance",
        "expire_execution",
        "approve_task",
        "auto_approve",
        "record_ruling",
    }
)
_BANK_EVENT_SOURCE: Final[str] = "central-bank"
_EVENT_TYPES: Final[dict[str, str]] = {
    "submit_bid": "bid.submitted",
    "accept_bid": "task.accepted",
    "cancel_task": "task.cancelled",
    "expire_bidding": "task.expired",
    "expire_acceptance": "task.expired",
    "submit_deliverable": "task.submitted",
    "expire_execution": "task.expired",
    "approve_task": "task.approved",
    "auto_approve": "task.auto_approved",
    "dispute_task": "task.disputed",
    "record_ruling": "task.ruled",
}


def _json_object(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _service_error(
    error: str,
    message: str,
    status_code: int,
    details: dict[str, object],
) -> ServiceError:
    return ServiceError(error, message, status_code, details)


class TaskOperationWriter:
    """Own one SQLite connection's atomic Board operation transactions."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        clock_us: Callable[[], int],
        deadline_actor_id: str,
        platform_public_key: str,
        *,
        current_task_board_caller_kid: str,
    ) -> None:
        if not isinstance(deadline_actor_id, str) or not deadline_actor_id:
            raise ValueError("deadline_actor_id must be a nonempty string")
        if not isinstance(platform_public_key, str) or not platform_public_key:
            raise ValueError("platform_public_key must be a nonempty string")
        if not isinstance(current_task_board_caller_kid, str) or not current_task_board_caller_kid:
            raise ValueError("current_task_board_caller_kid must be a nonempty string")
        self._db = connection
        self._clock_us = clock_us
        self._deadline_actor_id = deadline_actor_id
        self._platform_public_key = platform_public_key
        self._current_task_board_caller_kid = current_task_board_caller_kid

    def claim_operation(
        self,
        request: TaskOperationRequest,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        """Resolve replay or atomically claim/complete one validated command."""
        caller_kid = self._caller_kid(verified_caller_kid)
        if caller_kid != self._current_task_board_caller_kid:
            raise _service_error(
                "operation_caller_mismatch",
                "Only the current Task Board caller may own task operations",
                403,
                {},
            )
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            publication = cursor.execute(
                "SELECT 1 FROM board_task_publications WHERE operation_id=?",
                (request.operation_id,),
            ).fetchone()
            if publication is not None:
                raise _service_error(
                    "operation_id_conflict",
                    "Operation identity already belongs to a task publication",
                    409,
                    {"winning_operation_id": request.operation_id},
                )
            existing = self._operation_row(cursor, request.operation_id)
            if existing is not None:
                envelope = self._replay(existing, request, caller_kid)
                self._db.rollback()
                return envelope

            claimed_at_us = self._sample_clock()
            assert_no_competing_task_work(cursor, request.task_id)
            task = self._task(cursor, request.task_id)
            contract = self._task_contract(cursor, request.task_id)
            self._validate_version_and_status(request, task, contract)
            self._validate_actor(cursor, request, task)

            envelope = self._dispatch_claim(
                cursor, request, caller_kid, claimed_at_us, task, contract
            )
            self._db.commit()
            return envelope
        except ServiceError:
            self._db.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise _service_error(
                "task_operation_persistence_failed",
                "Task operation could not be persisted atomically",
                500,
                {},
            ) from exc
        except sqlite3.DatabaseError as exc:
            self._db.rollback()
            raise _service_error(
                "task_operation_persistence_failed",
                "Task operation could not be persisted atomically",
                500,
                {},
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def _dispatch_claim(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        if request.command == "submit_bid":
            return self._submit_bid(cursor, request, caller_kid, now_us, task, contract)
        if request.command == "accept_bid":
            return self._accept_bid(cursor, request, caller_kid, now_us, task, contract)
        if request.command == "submit_deliverable":
            return self._submit_deliverable(cursor, request, caller_kid, now_us, task, contract)
        if request.command == "dispute_task":
            return self._claim_court_effect(cursor, request, caller_kid, now_us, task, contract)
        if request.command in _BANK_COMMANDS:
            return self._claim_bank_effect(cursor, request, caller_kid, now_us, task, contract)
        raise _service_error(
            "unsupported_operation",
            "Task operation command is not supported by this writer",
            501,
            {"command": request.command},
        )

    def finalize_operation(
        self,
        operation_id: str,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        """Finalize only from authoritative evidence stored in this Gateway."""
        parsed_operation_id = parse_operation_id(operation_id)
        caller_kid = self._caller_kid(verified_caller_kid)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            operation = self._operation_row(cursor, parsed_operation_id)
            if operation is None:
                raise _service_error(
                    "operation_not_found",
                    "No task operation with this operation_id",
                    404,
                    {"operation_id": parsed_operation_id},
                )
            if str(operation[6]) != caller_kid:
                raise _service_error(
                    "operation_caller_mismatch",
                    "Only the original verified Gateway caller can finalize this operation",
                    403,
                    {"operation_id": parsed_operation_id},
                )
            effect_kind = str(operation[15])
            if effect_kind not in {"bank_release", "bank_split", "court_claim"}:
                raise _service_error(
                    "operation_not_finalizable",
                    "This task operation has no finalizable external effect",
                    409,
                    {"operation_id": parsed_operation_id},
                )
            if str(operation[14]) == "completed":
                envelope = self._envelope(operation)
                self._db.rollback()
                return envelope
            if str(operation[14]) == "blocked":
                self._raise_stored_error(operation)
            completed_at_us = self._sample_clock()
            task = self._task(cursor, str(operation[2]))
            contract = self._task_contract(cursor, str(operation[2]))
            if effect_kind == "court_claim":
                return self._finalize_court_operation(
                    cursor, operation, task, contract, completed_at_us
                )

            evidence, winning_operation_id = self._financial_evidence(
                cursor, operation, task, contract
            )
            if evidence is None:
                envelope = self._envelope(operation)
                self._db.rollback()
                return envelope
            if evidence is False:
                self._block_evidence_conflict(cursor, operation, winning_operation_id)
            try:
                self._validate_claim_ownership(operation, task, contract)
            except ServiceError:
                self._block_evidence_conflict(cursor, operation, winning_operation_id)

            result = self._complete_bank_effect(
                cursor,
                operation,
                task,
                contract,
                completed_at_us,
            )
            self._complete_native_delivery(cursor, operation, completed_at_us)
            self._db.commit()
            return result
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise

    def _finalize_court_operation(
        self,
        cursor: sqlite3.Cursor,
        operation: tuple[Any, ...],
        task: dict[str, Any],
        contract: dict[str, Any],
        completed_at_us: int,
    ) -> dict[str, Any]:
        self._validate_claim_ownership(operation, task, contract)
        evidence = self._court_evidence(cursor, operation, task, contract)
        if evidence is None:
            envelope = self._envelope(operation)
            self._db.rollback()
            return envelope
        if evidence is False:
            self._block_evidence_conflict(cursor, operation, None)
        assert isinstance(evidence, CourtClaimEvidence)
        result = self._complete_court_effect(
            cursor,
            operation,
            task,
            evidence,
            completed_at_us,
        )
        self._db.commit()
        return result

    @staticmethod
    def _complete_native_delivery(
        cursor: sqlite3.Cursor,
        operation: tuple[Any, ...],
        completed_at_us: int,
    ) -> None:
        operation_id = str(operation[0])
        receipt = cursor.execute(
            "SELECT event_id FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if receipt is None or type(receipt[0]) is not int:
            raise _service_error(
                "effect_evidence_conflict",
                "Authoritative receipt event is missing",
                409,
                {"operation_id": operation_id},
            )
        cursor.execute(
            "UPDATE board_task_effect_deliveries SET delivery_state='completed',"
            "next_attempt_at_us=NULL,lease_owner_id=NULL,lease_acquired_at_us=NULL,"
            "lease_expires_at_us=NULL,next_evidence_check_at_us=NULL,completed_at_us=?,"
            "completion_event_id=? WHERE native_operation_id=?",
            (completed_at_us, int(receipt[0]), operation_id),
        )
        if cursor.rowcount != 1:
            raise _service_error(
                "effect_delivery_missing",
                "Native financial operation has no delivery sidecar",
                500,
                {"operation_id": operation_id},
            )

    @staticmethod
    def _caller_kid(value: object) -> str:
        if not isinstance(value, str) or not value:
            raise _service_error("forbidden", "Verified Gateway caller is required", 403, {})
        return value

    def _sample_clock(self) -> int:
        value = self._clock_us()
        if type(value) is not int or value < 0:
            raise _service_error(
                "invalid_clock",
                "Task operation clock returned an invalid integer timestamp",
                500,
                {},
            )
        epoch_us_to_utc_text(value)
        return value

    @staticmethod
    def _operation_row(cursor: sqlite3.Cursor, operation_id: str) -> tuple[Any, ...] | None:
        row = cursor.execute(
            "SELECT operation_id, operation_contract_version, task_id, command, "
            "canonical_request_json, request_sha256, caller_kid, actor_kind, actor_id, "
            "source_status, source_lifecycle_version, target_status, "
            "target_lifecycle_version, claimed_at_us, operation_state, effect_kind, "
            "effect_state, effect_operation_id, effect_result_reference, effect_intent_json, "
            "completed_at_us, event_id, result_json, error_json "
            "FROM board_task_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    def _replay(
        self,
        operation: tuple[Any, ...],
        request: TaskOperationRequest,
        caller_kid: str,
    ) -> dict[str, Any]:
        if str(operation[6]) != caller_kid:
            raise _service_error(
                "operation_caller_mismatch",
                "Task operation identity belongs to another verified Gateway caller",
                403,
                {"operation_id": request.operation_id},
            )
        if str(operation[5]) != request.request_sha256:
            raise _service_error(
                "operation_id_conflict",
                "Operation identity already belongs to a different task command",
                409,
                {"winning_operation_id": request.operation_id},
            )
        if str(operation[14]) == "blocked":
            self._raise_stored_error(operation)
        return self._envelope(operation)

    @staticmethod
    def _raise_stored_error(operation: tuple[Any, ...]) -> None:
        raw = operation[23]
        if not isinstance(raw, str):
            raise _service_error(
                "operation_state_corrupt",
                "Blocked task operation has no durable error",
                500,
                {"operation_id": str(operation[0])},
            )
        decoded: object = json.loads(raw)
        if not isinstance(decoded, dict):
            raise _service_error(
                "operation_state_corrupt",
                "Blocked task operation has invalid durable error",
                500,
                {"operation_id": str(operation[0])},
            )
        error = decoded.get("error")
        message = decoded.get("message")
        details = decoded.get("details")
        if (
            not isinstance(error, str)
            or not isinstance(message, str)
            or not isinstance(details, dict)
        ):
            raise _service_error(
                "operation_state_corrupt",
                "Blocked task operation has invalid durable error fields",
                500,
                {"operation_id": str(operation[0])},
            )
        safe_details = {str(key): value for key, value in details.items() if isinstance(key, str)}
        raise _service_error(error, message, 409, safe_details)

    @staticmethod
    def _task(cursor: sqlite3.Cursor, task_id: str) -> dict[str, Any]:
        row = cursor.execute(
            "SELECT task_id, poster_id, reward, status, escrow_id, worker_id, accepted_bid_id, "
            "deadline_seconds, review_deadline_seconds, dispute_reason, dispute_id, "
            "rebuttal_deadline FROM board_tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise _service_error("task_not_found", "Task not found", 404, {})
        return {
            "task_id": str(row[0]),
            "poster_id": str(row[1]),
            "reward": int(row[2]),
            "status": str(row[3]),
            "escrow_id": str(row[4]),
            "worker_id": None if row[5] is None else str(row[5]),
            "accepted_bid_id": None if row[6] is None else str(row[6]),
            "deadline_seconds": int(row[7]),
            "review_deadline_seconds": int(row[8]),
            "dispute_reason": None if row[9] is None else str(row[9]),
            "dispute_id": None if row[10] is None else str(row[10]),
            "rebuttal_deadline": None if row[11] is None else str(row[11]),
        }

    @staticmethod
    def _task_contract(cursor: sqlite3.Cursor, task_id: str) -> dict[str, Any]:
        row = cursor.execute(
            "SELECT c.lifecycle_version, c.bidding_cutoff_us, c.acceptance_cutoff_us, "
            "c.execution_cutoff_us, c.review_cutoff_us, c.accepted_reward, "
            "c.accepted_bid_amount, p.pricing_rule_version "
            "FROM board_task_contracts AS c "
            "LEFT JOIN board_task_pricing AS p ON p.task_id = c.task_id WHERE c.task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise _service_error(
                "task_contract_missing",
                "Task has no authoritative lifecycle contract",
                500,
                {"task_id": task_id},
            )
        return {
            "lifecycle_version": int(row[0]),
            "bidding_cutoff_us": int(row[1]),
            "acceptance_cutoff_us": int(row[2]),
            "execution_cutoff_us": None if row[3] is None else int(row[3]),
            "review_cutoff_us": None if row[4] is None else int(row[4]),
            "accepted_reward": None if row[5] is None else int(row[5]),
            "accepted_bid_amount": None if row[6] is None else int(row[6]),
            "pricing_rule_version": None if row[7] is None else int(row[7]),
        }

    @staticmethod
    def _validate_version_and_status(
        request: TaskOperationRequest,
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> None:
        actual_version = int(contract["lifecycle_version"])
        if actual_version != request.expected_lifecycle_version:
            raise _service_error(
                "task_version_conflict",
                "Task lifecycle version differs from the command precondition",
                409,
                {
                    "expected_lifecycle_version": request.expected_lifecycle_version,
                    "actual_lifecycle_version": actual_version,
                    "current_status": str(task["status"]),
                },
            )
        if task["status"] != request.expected_status:
            raise _service_error(
                "invalid_status",
                "Task status differs from the command precondition",
                409,
                {"expected_status": request.expected_status, "current_status": task["status"]},
            )

    def _validate_actor(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        task: dict[str, Any],
    ) -> None:
        if request.actor_kind == "system":
            if request.actor_id != self._deadline_actor_id:
                raise _service_error("forbidden", "Invalid Task Board deadline actor", 403, {})
            return
        if request.command == "record_ruling":
            platform_actor = configured_platform_actor(cursor, self._platform_public_key)
            if request.actor_id != platform_actor:
                raise _service_error("forbidden", "Invalid platform ruling actor", 403, {})
            return
        expected_actor = (
            task["worker_id"] if request.command == "submit_deliverable" else task["poster_id"]
        )
        if request.command == "submit_bid":
            expected_actor = request.argument("bidder_id")
        if request.actor_id != expected_actor:
            raise _service_error(
                "forbidden", "Task operation actor does not own this action", 403, {}
            )

    @staticmethod
    def _before(now_us: int, cutoff_us: int) -> None:
        if now_us >= cutoff_us:
            raise _service_error(
                "cutoff_reached", "Task operation cutoff has been reached", 409, {}
            )

    @staticmethod
    def _at_or_after(now_us: int, cutoff_us: int) -> None:
        if now_us < cutoff_us:
            raise _service_error(
                "cutoff_not_reached", "Task operation cutoff has not been reached", 409, {}
            )

    def _submit_bid(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        self._before(now_us, int(contract["bidding_cutoff_us"]))
        bidder_id = str(request.argument("bidder_id"))
        if request.actor_id != bidder_id:
            raise _service_error("forbidden", "Bidder must match the operation actor", 403, {})
        if bidder_id == task["poster_id"]:
            raise _service_error("self_bid", "Cannot bid on your own task", 400, {})
        bid_id = str(request.argument("bid_id"))
        amount = int(request.argument("amount"))
        self._require_bid_within_budget(amount, task)
        collision = cursor.execute(
            "SELECT 1 FROM board_bids WHERE bid_id = ? OR (task_id = ? AND bidder_id = ?) LIMIT 1",
            (bid_id, request.task_id, bidder_id),
        ).fetchone()
        if collision is not None:
            raise _service_error("bid_exists", "Bid already exists", 409, {})
        try:
            cursor.execute(
                "INSERT INTO board_bids "
                "(bid_id, task_id, bidder_id, proposal, amount, submitted_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    bid_id,
                    request.task_id,
                    bidder_id,
                    str(amount),
                    amount,
                    epoch_us_to_utc_text(now_us),
                ),
            )
        except sqlite3.IntegrityError as exc:
            if is_unique_violation(exc):
                raise _service_error("bid_exists", "Bid already exists", 409, {}) from exc
            if is_foreign_key_violation(exc):
                raise _service_error(
                    "foreign_key_violation", "Bid references an unknown task or bidder", 409, {}
                ) from exc
            raise
        cursor.execute(
            "UPDATE board_tasks SET bid_count = bid_count + 1 WHERE task_id = ?",
            (request.task_id,),
        )
        result: dict[str, object] = {
            "task_id": request.task_id,
            "command": request.command,
            "status": "open",
            "lifecycle_version": request.expected_lifecycle_version,
            "bid_id": bid_id,
            "bidder_id": bidder_id,
            "amount": amount,
        }
        return self._persist_pure(cursor, request, caller_kid, now_us, result)

    def _accept_bid(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        self._before(now_us, int(contract["acceptance_cutoff_us"]))
        self._require_task_arguments(
            request, task, include_worker=False, expected_reward=int(task["reward"])
        )
        bid_id = str(request.argument("bid_id"))
        row = cursor.execute(
            "SELECT bidder_id, amount FROM board_bids WHERE bid_id = ? AND task_id = ?",
            (bid_id, request.task_id),
        ).fetchone()
        if row is None:
            raise _service_error("bid_not_found", "Bid not found", 404, {})
        worker_id = str(row[0])
        bid_amount = int(row[1])
        if worker_id != request.argument("worker_id") or bid_amount != request.argument(
            "bid_amount"
        ):
            raise _service_error("payload_mismatch", "Selected bid facts do not match", 409, {})
        self._require_bid_within_budget(bid_amount, task)
        execution_cutoff_us = add_seconds_to_epoch_us(now_us, task["deadline_seconds"])
        accepted_at = epoch_us_to_utc_text(now_us)
        binding = inspect_acceptance_binding(
            cursor,
            task_id=request.task_id,
            poster_id=str(task["poster_id"]),
            worker_id=worker_id,
            bid_id=bid_id,
            bid_amount=bid_amount,
            accepted_at_us=now_us,
            execution_cutoff_us=execution_cutoff_us,
            target_lifecycle_version=request.expected_lifecycle_version + 1,
        )
        try:
            return self._commit_acceptance(
                cursor,
                request,
                caller_kid,
                now_us,
                task,
                binding,
                worker_id,
                bid_id,
                bid_amount,
                execution_cutoff_us,
                accepted_at,
            )
        except ServiceError as exc:
            if exc.error != "event_insert_failed":
                raise
            raise _service_error(
                "task_operation_persistence_failed",
                "Task operation could not be persisted atomically",
                500,
                {},
            ) from exc
        except RuntimeError as exc:
            raise _service_error(
                "task_operation_persistence_failed",
                "Task operation could not be persisted atomically",
                500,
                {},
            ) from exc

    def _commit_acceptance(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        task: dict[str, Any],
        binding: AcceptedArtifactBinding | None,
        worker_id: str,
        bid_id: str,
        bid_amount: int,
        execution_cutoff_us: int,
        accepted_at: str,
    ) -> dict[str, Any]:
        cursor.execute(
            "UPDATE board_tasks SET status='accepted', worker_id=?, accepted_bid_id=?, "
            "accepted_at=?, execution_deadline=? WHERE task_id=? AND status='open'",
            (
                worker_id,
                bid_id,
                accepted_at,
                epoch_us_to_utc_text(execution_cutoff_us),
                request.task_id,
            ),
        )
        if cursor.rowcount != 1:
            raise _service_error("task_version_conflict", "Task changed during acceptance", 409, {})
        cursor.execute(
            "UPDATE board_task_contracts SET lifecycle_version=?, execution_cutoff_us=?, "
            "accepted_reward=?, accepted_bid_amount=? WHERE task_id=? AND lifecycle_version=?",
            (
                request.expected_lifecycle_version + 1,
                execution_cutoff_us,
                task["reward"],
                bid_amount,
                request.task_id,
                request.expected_lifecycle_version,
            ),
        )
        if cursor.rowcount != 1:
            raise _service_error("task_version_conflict", "Task changed during acceptance", 409, {})
        if binding is not None:
            commit_acceptance_binding(cursor, binding=binding)
        result: dict[str, object] = {
            "task_id": request.task_id,
            "command": request.command,
            "status": "accepted",
            "lifecycle_version": request.expected_lifecycle_version + 1,
            "bid_id": bid_id,
            "worker_id": worker_id,
            "reward": int(task["reward"]),
            "bid_amount": bid_amount,
        }
        if binding is not None and not isinstance(binding, EmptyInputAcceptanceBinding):
            result["accepted_binding_kind"] = binding.kind
            result["accepted_binding_sha256"] = binding.sha256
        return self._persist_pure(cursor, request, caller_kid, now_us, result)

    def _submit_deliverable(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        cutoff = contract["execution_cutoff_us"]
        if not isinstance(cutoff, int):
            raise _service_error("task_contract_invalid", "Execution cutoff is missing", 500, {})
        self._before(now_us, cutoff)
        worker_id = task["worker_id"]
        if worker_id != request.argument("worker_id"):
            raise _service_error("payload_mismatch", "Worker facts do not match", 409, {})
        frozen_manifest = None
        if request.protocol_version == 2:
            frozen_manifest = freeze_submission_manifest(cursor, request, now_us)
        else:
            reject_governed_protocol_v1_submission(cursor, request.task_id)
            asset = cursor.execute(
                "SELECT 1 FROM board_assets WHERE task_id = ? AND uploader_id = ? LIMIT 1",
                (request.task_id, worker_id),
            ).fetchone()
            if asset is None:
                raise _service_error(
                    "no_assets", "At least one worker asset is required before submission", 400, {}
                )
        review_cutoff_us = add_seconds_to_epoch_us(now_us, task["review_deadline_seconds"])
        submitted_at = epoch_us_to_utc_text(now_us)
        if frozen_manifest is not None:
            try:
                return self._commit_governed_submission(
                    cursor,
                    request,
                    caller_kid,
                    now_us,
                    worker_id=str(worker_id),
                    review_cutoff_us=review_cutoff_us,
                    submitted_at=submitted_at,
                    frozen_manifest=frozen_manifest,
                )
            except ServiceError as exc:
                if exc.error != "event_insert_failed":
                    raise
                raise _service_error(
                    "task_operation_persistence_failed",
                    "Task operation could not be persisted atomically",
                    500,
                    {},
                ) from exc
            except RuntimeError as exc:
                raise _service_error(
                    "task_operation_persistence_failed",
                    "Task operation could not be persisted atomically",
                    500,
                    {},
                ) from exc
        cursor.execute(
            "UPDATE board_tasks SET status='submitted', submitted_at=?, review_deadline=? "
            "WHERE task_id=? AND status='accepted'",
            (submitted_at, epoch_us_to_utc_text(review_cutoff_us), request.task_id),
        )
        cursor.execute(
            "UPDATE board_task_contracts SET lifecycle_version=?, review_cutoff_us=? "
            "WHERE task_id=? AND lifecycle_version=?",
            (
                request.expected_lifecycle_version + 1,
                review_cutoff_us,
                request.task_id,
                request.expected_lifecycle_version,
            ),
        )
        result: dict[str, object] = {
            "task_id": request.task_id,
            "command": request.command,
            "status": "submitted",
            "lifecycle_version": request.expected_lifecycle_version + 1,
            "worker_id": str(worker_id),
        }
        return self._persist_pure(cursor, request, caller_kid, now_us, result)

    def _commit_governed_submission(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        *,
        worker_id: str,
        review_cutoff_us: int,
        submitted_at: str,
        frozen_manifest: FrozenSubmissionManifest,
    ) -> dict[str, Any]:
        cursor.execute(
            "UPDATE board_tasks SET status='submitted', submitted_at=?, review_deadline=? "
            "WHERE task_id=? AND status='accepted'",
            (submitted_at, epoch_us_to_utc_text(review_cutoff_us), request.task_id),
        )
        if cursor.rowcount != 1:
            raise _service_error("task_version_conflict", "Task changed during submission", 409, {})
        cursor.execute(
            "UPDATE board_task_contracts SET lifecycle_version=?, review_cutoff_us=? "
            "WHERE task_id=? AND lifecycle_version=?",
            (
                request.expected_lifecycle_version + 1,
                review_cutoff_us,
                request.task_id,
                request.expected_lifecycle_version,
            ),
        )
        if cursor.rowcount != 1:
            raise _service_error("task_version_conflict", "Task changed during submission", 409, {})
        result: dict[str, object] = {
            "task_id": request.task_id,
            "command": request.command,
            "status": "submitted",
            "lifecycle_version": request.expected_lifecycle_version + 1,
            "worker_id": worker_id,
            "manifest_id": frozen_manifest.manifest_id,
            "manifest_sha256": frozen_manifest.manifest_sha256,
        }
        return self._persist_pure(cursor, request, caller_kid, now_us, result)

    def _claim_bank_effect(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        if request.command != "record_ruling":
            self._validate_bank_eligibility(cursor, request, now_us, contract)
        effect_kind, intent = self._build_financial_effect(cursor, request, task, contract)
        expected_escrow = intent["expected_escrow"]
        assert isinstance(expected_escrow, dict)
        if not locked_escrow_matches(cursor, expected_escrow):
            raise _service_error("payload_mismatch", "Financial escrow facts do not match", 409, {})
        self._insert_operation(
            cursor,
            request,
            caller_kid,
            now_us,
            operation_state="claimed",
            effect_kind=effect_kind,
            effect_state="pending",
            effect_operation_id=request.operation_id,
            effect_intent=intent,
            event_id=None,
            result=None,
        )
        cursor.execute(
            "INSERT INTO board_task_effect_deliveries "
            "(effect_operation_id,recovery_contract_version,task_id,source_kind,"
            "native_operation_id,legacy_observation_id,owner_caller_kid,effect_kind,"
            "delivery_state,enqueued_at_us,total_attempt_count,cycle_attempt_count,"
            "lease_generation,next_attempt_at_us,lease_owner_id,lease_acquired_at_us,"
            "lease_expires_at_us,last_failure_code,last_failure_at_us,operator_retry_count,"
            "last_operator_retry_at_us,last_operator_retry_caller_kid,"
            "next_evidence_check_at_us,completed_at_us,completion_event_id,cutover_singleton) "
            "VALUES (?,1,?,'native_task_operation',?,NULL,?,?,"
            "'pending',?,0,0,0,?,NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL)",
            (
                request.operation_id,
                request.task_id,
                request.operation_id,
                caller_kid,
                effect_kind,
                now_us,
                now_us,
            ),
        )
        row = self._operation_row(cursor, request.operation_id)
        if row is None:
            raise _service_error(
                "task_operation_persistence_failed",
                "Task operation disappeared after persistence",
                500,
                {},
            )
        return self._envelope(row)

    def _claim_court_effect(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        cutoff = contract["review_cutoff_us"]
        if not isinstance(cutoff, int):
            raise _service_error("task_contract_invalid", "Review cutoff is missing", 500, {})
        self._before(now_us, cutoff)
        intent = build_court_claim_intent(request, task, contract)
        expected_escrow = intent["expected_escrow"]
        assert isinstance(expected_escrow, dict)
        if not locked_escrow_matches(cursor, expected_escrow):
            raise _service_error("payload_mismatch", "Court escrow facts do not match", 409, {})
        self._insert_operation(
            cursor,
            request,
            caller_kid,
            now_us,
            operation_state="claimed",
            effect_kind="court_claim",
            effect_state="pending",
            effect_operation_id=None,
            effect_intent=intent,
            event_id=None,
            result=None,
        )
        row = self._operation_row(cursor, request.operation_id)
        if row is None:
            raise _service_error(
                "task_operation_persistence_failed",
                "Task operation disappeared after persistence",
                500,
                {},
            )
        return self._envelope(row)

    def _build_financial_effect(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> tuple[str, dict[str, object]]:
        expected_source_reference = self._financial_source_reference(request)
        if request.argument("source_reference") != expected_source_reference:
            raise _service_error(
                "payload_mismatch", "Financial source reference does not match", 409, {}
            )
        accepted_reward = contract["accepted_reward"]
        expected_reward = task["reward"] if accepted_reward is None else accepted_reward
        include_worker = request.command in {
            "expire_execution",
            "approve_task",
            "auto_approve",
            "record_ruling",
        }
        self._require_task_arguments(
            request,
            task,
            include_worker=include_worker,
            expected_reward=int(expected_reward),
        )
        if request.command == "record_ruling":
            self._validate_ruling_linkage(cursor, request, task)
        worker_amount = self._settlement_worker_amount(request, contract)
        try:
            operation_kind, fields = settlement_request_fields(
                escrow_id=str(task["escrow_id"]),
                poster_id=str(task["poster_id"]),
                worker_id=None if task["worker_id"] is None else str(task["worker_id"]),
                escrow_amount=int(expected_reward),
                worker_amount=worker_amount,
                source_reference=expected_source_reference,
                ruling_pct=(
                    int(request.argument("worker_pct"))
                    if request.command == "record_ruling"
                    else None
                ),
            )
        except ValueError as exc:
            raise _service_error(
                "task_contract_invalid", "Accepted price does not fit the escrow", 500, {}
            ) from exc
        effect_kind = "bank_release" if operation_kind == "escrow_release" else "bank_split"
        financial = canonical_request(operation_kind, fields)
        return effect_kind, {
            "financial_request": json.loads(financial.canonical_json),
            "financial_request_sha256": financial.request_sha256,
            "expected_escrow": {
                "escrow_id": str(task["escrow_id"]),
                "task_id": request.task_id,
                "payer_account_id": str(task["poster_id"]),
                "amount": int(expected_reward),
            },
        }

    @staticmethod
    def _settlement_worker_amount(
        request: TaskOperationRequest,
        contract: dict[str, Any],
    ) -> int:
        """Return the worker's share of the final settlement under accepted-bid pricing."""
        if request.command not in {"approve_task", "auto_approve", "record_ruling"}:
            return 0
        accepted_price = contract["accepted_bid_amount"]
        rule_version = contract["pricing_rule_version"]
        if not isinstance(accepted_price, int) or not isinstance(rule_version, int):
            raise _service_error(
                "task_contract_invalid", "Accepted price or pricing rule is missing", 500, {}
            )
        worker_pct = (
            int(request.argument("worker_pct")) if request.command == "record_ruling" else None
        )
        try:
            return worker_amount_for(
                rule_version, accepted_price=accepted_price, worker_pct=worker_pct
            )
        except ValueError as exc:
            raise _service_error(
                "task_contract_invalid", "Unknown pricing rule version", 500, {}
            ) from exc

    @staticmethod
    def _require_bid_within_budget(amount: int, task: dict[str, Any]) -> None:
        if amount > int(task["reward"]):
            raise _service_error(
                "bid_exceeds_budget",
                "Bid amount must not exceed the posted reward",
                400,
                {"reward": int(task["reward"])},
            )

    @staticmethod
    def _validate_ruling_linkage(
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        task: dict[str, Any],
    ) -> None:
        claim_id = str(request.argument("ruling_id"))
        claim = cursor.execute(
            "SELECT task_id, claimant_id, respondent_id, reason FROM court_claims "
            "WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        expected_claim = (
            request.task_id,
            task["poster_id"],
            task["worker_id"],
            task["dispute_reason"],
        )
        if task["dispute_id"] != claim_id or claim is None or tuple(claim) != expected_claim:
            raise _service_error("payload_mismatch", "Ruling linkage does not match", 409, {})

    @staticmethod
    def _financial_source_reference(request: TaskOperationRequest) -> str:
        if request.command == "record_ruling":
            return f"task:{request.task_id}:ruling:{request.argument('ruling_id')}"
        action = {
            "cancel_task": "cancelled",
            "expire_bidding": "expired",
            "expire_acceptance": "expired",
            "expire_execution": "expired",
            "approve_task": "approved",
            "auto_approve": "approved",
        }.get(request.command)
        if action is None:
            raise _service_error(
                "unsupported_operation",
                "Task operation has no financial source-reference contract",
                501,
                {"command": request.command},
            )
        return f"task:{request.task_id}:{action}"

    def _validate_bank_eligibility(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        now_us: int,
        contract: dict[str, Any],
    ) -> None:
        command = request.command
        bid_exists = cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM board_bids WHERE task_id = ?)",
            (request.task_id,),
        ).fetchone()
        has_bid = bool(bid_exists is not None and int(bid_exists[0]) == 1)
        if command == "cancel_task":
            cutoff = int(
                contract["acceptance_cutoff_us"] if has_bid else contract["bidding_cutoff_us"]
            )
            self._require_cutoff_argument(request, cutoff)
            self._before(now_us, cutoff)
        elif command == "expire_bidding":
            if has_bid:
                raise _service_error("invalid_status", "Bidding expiry requires no bids", 409, {})
            cutoff = int(contract["bidding_cutoff_us"])
            self._require_cutoff_argument(request, cutoff)
            self._at_or_after(now_us, cutoff)
        elif command == "expire_acceptance":
            if not has_bid:
                raise _service_error("invalid_status", "Acceptance expiry requires a bid", 409, {})
            cutoff = int(contract["acceptance_cutoff_us"])
            self._require_cutoff_argument(request, cutoff)
            self._at_or_after(now_us, cutoff)
        elif command == "expire_execution":
            cutoff = contract["execution_cutoff_us"]
            if not isinstance(cutoff, int):
                raise _service_error(
                    "task_contract_invalid", "Execution cutoff is missing", 500, {}
                )
            self._require_cutoff_argument(request, cutoff)
            self._at_or_after(now_us, cutoff)
        elif command == "approve_task":
            cutoff = contract["review_cutoff_us"]
            if not isinstance(cutoff, int):
                raise _service_error("task_contract_invalid", "Review cutoff is missing", 500, {})
            self._before(now_us, cutoff)
        elif command == "auto_approve":
            cutoff = contract["review_cutoff_us"]
            if not isinstance(cutoff, int):
                raise _service_error("task_contract_invalid", "Review cutoff is missing", 500, {})
            self._require_cutoff_argument(request, cutoff)
            self._at_or_after(now_us, cutoff)

    @staticmethod
    def _require_cutoff_argument(request: TaskOperationRequest, expected: int) -> None:
        if request.argument("cutoff_us") != expected:
            raise _service_error("payload_mismatch", "Frozen cutoff does not match", 409, {})

    @staticmethod
    def _require_task_arguments(
        request: TaskOperationRequest,
        task: dict[str, Any],
        *,
        include_worker: bool,
        expected_reward: int,
    ) -> None:
        if "poster_id" in request.arguments and request.argument("poster_id") != task["poster_id"]:
            raise _service_error("payload_mismatch", "Poster facts do not match", 409, {})
        if "escrow_id" in request.arguments and request.argument("escrow_id") != task["escrow_id"]:
            raise _service_error("payload_mismatch", "Escrow facts do not match", 409, {})
        if "reward" in request.arguments and request.argument("reward") != expected_reward:
            raise _service_error("payload_mismatch", "Reward facts do not match", 409, {})
        if include_worker and request.argument("worker_id") != task["worker_id"]:
            raise _service_error("payload_mismatch", "Worker facts do not match", 409, {})

    def _persist_pure(
        self,
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        result: dict[str, object],
    ) -> dict[str, Any]:
        event_id = self._insert_event(cursor, request, now_us)
        result["event_id"] = event_id
        self._insert_operation(
            cursor,
            request,
            caller_kid,
            now_us,
            operation_state="completed",
            effect_kind="none",
            effect_state="none",
            effect_operation_id=None,
            effect_intent=None,
            event_id=event_id,
            result=result,
        )
        row = self._operation_row(cursor, request.operation_id)
        if row is None:
            raise _service_error(
                "task_operation_persistence_failed",
                "Task operation disappeared after persistence",
                500,
                {},
            )
        return self._envelope(row)

    @staticmethod
    def _insert_event(cursor: sqlite3.Cursor, request: TaskOperationRequest, now_us: int) -> int:
        event_type = _EVENT_TYPES[request.command]
        timestamp = epoch_us_to_utc_text(now_us)
        agent_id = request.actor_id if request.actor_kind == "agent" else None
        summary = f"Task operation completed: {request.command}"
        payload = _json_object({"operation_id": request.operation_id, "command": request.command})
        cursor.execute(
            "INSERT INTO events "
            "(event_source, event_type, timestamp, task_id, agent_id, summary, payload) "
            "VALUES ('board', ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                timestamp,
                request.task_id,
                agent_id,
                summary,
                payload,
            ),
        )
        if cursor.rowcount != 1:
            raise _service_error(
                "task_operation_persistence_failed",
                "Task event was not persisted",
                500,
                {},
            )
        if type(cursor.lastrowid) is not int or cursor.lastrowid <= 0:
            raise _service_error("event_insert_failed", "Task event was not persisted", 500, {})
        event_id = cursor.lastrowid
        linked = cursor.execute(
            "SELECT event_source,event_type,timestamp,task_id,agent_id,summary,payload "
            "FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if linked is None or tuple(linked) != (
            "board",
            event_type,
            timestamp,
            request.task_id,
            agent_id,
            summary,
            payload,
        ):
            raise _service_error("event_insert_failed", "Task event was not persisted", 500, {})
        return event_id

    @staticmethod
    def _insert_operation(
        cursor: sqlite3.Cursor,
        request: TaskOperationRequest,
        caller_kid: str,
        now_us: int,
        *,
        operation_state: str,
        effect_kind: str,
        effect_state: str,
        effect_operation_id: str | None,
        effect_intent: dict[str, object] | None,
        event_id: int | None,
        result: dict[str, object] | None,
    ) -> None:
        target_version = request.expected_lifecycle_version
        if request.command != "submit_bid":
            target_version += 1
        cursor.execute(
            "INSERT INTO board_task_operations "
            "(operation_id, operation_contract_version, task_id, command, "
            "canonical_request_json, request_sha256, caller_kid, actor_kind, actor_id, "
            "source_status, source_lifecycle_version, target_status, "
            "target_lifecycle_version, operation_state, effect_kind, effect_state, "
            "effect_operation_id, effect_result_reference, effect_intent_json, claimed_at_us, "
            "completed_at_us, event_id, result_json, error_json) "
            "VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, NULL)",
            (
                request.operation_id,
                request.task_id,
                request.command,
                request.canonical_json,
                request.request_sha256,
                caller_kid,
                request.actor_kind,
                request.actor_id,
                request.expected_status,
                request.expected_lifecycle_version,
                COMMAND_TARGET_STATUS[request.command],
                target_version,
                operation_state,
                effect_kind,
                effect_state,
                effect_operation_id,
                None if effect_intent is None else _json_object(effect_intent),
                now_us,
                now_us if operation_state == "completed" else None,
                event_id,
                None if result is None else _json_object(result),
            ),
        )
        if cursor.rowcount != 1:
            raise _service_error(
                "task_operation_persistence_failed",
                "Task operation was not persisted",
                500,
                {},
            )

    def _financial_evidence(
        self,
        cursor: sqlite3.Cursor,
        operation: tuple[Any, ...],
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> tuple[bool | None, str | None]:
        operation_id = str(operation[0])
        expected_intent = self._expected_financial_intent(cursor, operation, task, contract)
        if expected_intent is None:
            return False, None
        intent_raw = operation[19]
        if not isinstance(intent_raw, str):
            return False, None
        intent: object = json.loads(intent_raw)
        if intent != expected_intent:
            return False, None
        expected_request = expected_intent["financial_request"]
        expected_hash = expected_intent["financial_request_sha256"]
        expected_escrow = expected_intent["expected_escrow"]
        assert isinstance(expected_request, dict)
        assert isinstance(expected_hash, str)
        assert isinstance(expected_escrow, dict)
        receipt = cursor.execute(
            "SELECT receipt_version, operation_kind, canonical_request_json, request_sha256, "
            "source_reference, escrow_id, result_json, event_id, committed_at "
            "FROM bank_operation_receipts "
            "WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        escrow_id = str(expected_request["escrow_id"])
        if receipt is None:
            return self._missing_receipt_state(cursor, escrow_id)
        return (
            self._receipt_matches(
                cursor,
                operation_id,
                tuple(receipt),
                expected_request,
                expected_hash,
                expected_escrow,
            ),
            None,
        )

    def _court_evidence(
        self,
        cursor: sqlite3.Cursor,
        operation: tuple[Any, ...],
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> CourtClaimEvidence | bool | None:
        request = self._request_from_operation(operation)
        try:
            expected_intent = build_court_claim_intent(request, task, contract)
        except ServiceError:
            return False
        intent_raw = operation[19]
        if not isinstance(intent_raw, str):
            return False
        stored_intent: object = json.loads(intent_raw)
        if stored_intent != expected_intent:
            return False
        evaluated = evaluate_court_claim(cursor, expected_intent)
        if evaluated.state == "pending":
            return None
        if evaluated.state == "conflict":
            return False
        return evaluated.evidence

    def _complete_court_effect(
        self,
        cursor: sqlite3.Cursor,
        operation: tuple[Any, ...],
        task: dict[str, Any],
        evidence: CourtClaimEvidence,
        completed_at_us: int,
    ) -> dict[str, Any]:
        request = self._request_from_operation(operation)
        timestamp = epoch_us_to_utc_text(completed_at_us)
        cursor.execute(
            "UPDATE board_tasks SET status='disputed', dispute_reason=?, dispute_id=?, "
            "rebuttal_deadline=?, disputed_at=? WHERE task_id=? AND status='submitted'",
            (
                request.argument("reason"),
                evidence.claim_id,
                evidence.rebuttal_deadline,
                timestamp,
                str(operation[2]),
            ),
        )
        if cursor.rowcount != 1:
            raise _service_error("operation_claim_lost", "Task operation claim was lost", 409, {})
        cursor.execute(
            "UPDATE board_task_contracts SET lifecycle_version=? "
            "WHERE task_id=? AND lifecycle_version=?",
            (int(operation[12]), str(operation[2]), int(operation[10])),
        )
        event_id = self._insert_event(cursor, request, completed_at_us)
        result: dict[str, object] = {
            "task_id": str(operation[2]),
            "command": str(operation[3]),
            "status": "disputed",
            "lifecycle_version": int(operation[12]),
            "event_id": event_id,
            "effect_result_reference": evidence.claim_id,
            "dispute_id": evidence.claim_id,
            "reason": str(request.argument("reason")),
            "worker_id": str(task["worker_id"]),
        }
        cursor.execute(
            "UPDATE board_task_operations SET operation_state='completed', "
            "effect_state='succeeded', effect_result_reference=?, completed_at_us=?, "
            "event_id=?, result_json=? WHERE operation_id=? AND operation_state='claimed'",
            (
                evidence.claim_id,
                completed_at_us,
                event_id,
                _json_object(result),
                str(operation[0]),
            ),
        )
        refreshed = self._operation_row(cursor, str(operation[0]))
        if refreshed is None:
            raise _service_error(
                "operation_state_corrupt", "Completed operation disappeared", 500, {}
            )
        return self._envelope(refreshed)

    def _expected_financial_intent(
        self,
        cursor: sqlite3.Cursor,
        operation: tuple[Any, ...],
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> dict[str, object] | None:
        request = self._request_from_operation(operation)
        try:
            _effect_kind, intent = self._build_financial_effect(cursor, request, task, contract)
        except ServiceError:
            return None
        return intent

    @staticmethod
    def _missing_receipt_state(
        cursor: sqlite3.Cursor, escrow_id: str
    ) -> tuple[bool | None, str | None]:
        winner = cursor.execute(
            "SELECT operation_id FROM bank_operation_receipts WHERE escrow_id = ? "
            "AND operation_kind IN ('escrow_release', 'escrow_split')",
            (escrow_id,),
        ).fetchone()
        escrow = cursor.execute(
            "SELECT status, resolved_at FROM bank_escrow WHERE escrow_id = ?",
            (escrow_id,),
        ).fetchone()
        compatible_pending = (
            winner is None
            and escrow is not None
            and str(escrow[0]) == "locked"
            and escrow[1] is None
        )
        if compatible_pending:
            return None, None
        return False, None if winner is None else str(winner[0])

    @staticmethod
    def _receipt_matches(
        cursor: sqlite3.Cursor,
        operation_id: str,
        receipt: tuple[Any, ...],
        expected_request: dict[object, object],
        expected_hash: str,
        expected_escrow: dict[object, object],
    ) -> bool:
        escrow_id = str(expected_request["escrow_id"])
        operation_kind = expected_request.get("operation_kind")
        canonical_json = _json_object({str(key): value for key, value in expected_request.items()})
        if (
            int(receipt[0]) != 1
            or str(receipt[1]) != operation_kind
            or str(receipt[2]) != canonical_json
            or str(receipt[3]) != expected_hash
            or str(receipt[4]) != expected_request.get("source_reference")
            or str(receipt[5]) != escrow_id
        ):
            return False
        result: object = json.loads(str(receipt[6]))
        event_id = int(receipt[7])
        event = cursor.execute(
            "SELECT event_source, event_type, task_id FROM events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        escrow = cursor.execute(
            "SELECT payer_account_id, task_id, amount, status, resolved_at FROM bank_escrow "
            "WHERE escrow_id = ?",
            (escrow_id,),
        ).fetchone()
        expected_status = "released" if operation_kind == "escrow_release" else "split"
        escrow_matches = escrow is not None and (
            str(escrow[0]) != expected_escrow.get("payer_account_id")
            or str(escrow[1]) != expected_escrow.get("task_id")
            or int(escrow[2]) != expected_escrow.get("amount")
            or str(escrow[3]) != expected_status
            or escrow[4] is None
        )
        legs = cursor.execute(
            "SELECT role, account_id, direction, amount, transaction_id, balance_after "
            "FROM bank_operation_legs "
            "WHERE operation_id = ? ORDER BY leg_index",
            (operation_id,),
        ).fetchall()
        if escrow is None or escrow_matches or escrow[4] != receipt[8]:
            return False
        expected_event_type = (
            "escrow.released" if operation_kind == "escrow_release" else "escrow.split"
        )
        if (
            event is None
            or str(event[0]) != _BANK_EVENT_SOURCE
            or str(event[1]) != expected_event_type
            or (event[2] is not None and event[2] != expected_escrow.get("task_id"))
        ):
            return False
        if operation_kind == "escrow_release":
            return TaskOperationWriter._release_receipt_matches(
                cursor,
                tuple(tuple(row) for row in legs),
                expected_request,
                expected_escrow,
                escrow_id,
                event_id,
                str(receipt[8]),
                result,
            )
        if operation_kind == "escrow_split":
            return TaskOperationWriter._split_receipt_matches(
                cursor,
                tuple(tuple(row) for row in legs),
                expected_request,
                expected_escrow,
                escrow_id,
                event_id,
                str(receipt[8]),
                result,
            )
        return False

    @staticmethod
    def _release_receipt_matches(
        cursor: sqlite3.Cursor,
        legs: tuple[tuple[Any, ...], ...],
        expected_request: dict[object, object],
        expected_escrow: dict[object, object],
        escrow_id: str,
        event_id: int,
        committed_at: str,
        result: object,
    ) -> bool:
        if len(legs) != 1 or result != {"escrow_status_at_commit": "released"}:
            return False
        return TaskOperationWriter._leg_provenance_matches(
            cursor,
            legs[0],
            role="recipient",
            account_id=expected_request.get("recipient_account_id"),
            amount=expected_escrow.get("amount"),
            escrow_id=escrow_id,
            event_id=event_id,
            committed_at=committed_at,
        )

    @staticmethod
    def _split_receipt_matches(
        cursor: sqlite3.Cursor,
        legs: tuple[tuple[Any, ...], ...],
        expected_request: dict[object, object],
        expected_escrow: dict[object, object],
        escrow_id: str,
        event_id: int,
        committed_at: str,
        result: object,
    ) -> bool:
        total = expected_escrow.get("amount")
        share_field = "worker_amount" if "worker_amount" in expected_request else "worker_pct"
        share = expected_request.get(share_field)
        if not isinstance(share, int) or not isinstance(total, int) or len(legs) != 2:
            return False
        worker_amount = share if share_field == "worker_amount" else total * share // 100
        poster_amount = total - worker_amount
        if poster_amount < 0:
            return False
        expected_result = {"escrow_status_at_commit": "split", share_field: share}
        return (
            result == expected_result
            and TaskOperationWriter._leg_provenance_matches(
                cursor,
                legs[0],
                role="worker",
                account_id=expected_request.get("worker_account_id"),
                amount=worker_amount,
                escrow_id=escrow_id,
                event_id=event_id,
                committed_at=committed_at,
            )
            and TaskOperationWriter._leg_provenance_matches(
                cursor,
                legs[1],
                role="poster",
                account_id=expected_request.get("poster_account_id"),
                amount=poster_amount,
                escrow_id=escrow_id,
                event_id=event_id,
                committed_at=committed_at,
            )
        )

    @staticmethod
    def _leg_provenance_matches(
        cursor: sqlite3.Cursor,
        leg: tuple[Any, ...],
        *,
        role: str,
        account_id: object,
        amount: object,
        escrow_id: str,
        event_id: int,
        committed_at: str,
    ) -> bool:
        expected_leg = (role, account_id, "credit", amount)
        transaction_id = leg[4]
        balance_after = leg[5]
        if leg[:4] != expected_leg:
            return False
        if amount == 0:
            return transaction_id is None and balance_after is None
        if not isinstance(transaction_id, str) or not isinstance(balance_after, int):
            return False
        transaction = cursor.execute(
            "SELECT account_id, type, amount, balance_after, reference, event_id, timestamp "
            "FROM bank_transactions WHERE tx_id = ?",
            (transaction_id,),
        ).fetchone()
        account = cursor.execute(
            "SELECT account_id FROM bank_accounts WHERE account_id = ?",
            (account_id,),
        ).fetchone()
        expected_transaction = (
            account_id,
            "escrow_release",
            amount,
            balance_after,
            escrow_id,
            event_id,
            committed_at,
        )
        return (
            transaction is not None
            and tuple(transaction) == expected_transaction
            and account is not None
        )

    def _block_evidence_conflict(
        self,
        cursor: sqlite3.Cursor,
        operation: tuple[Any, ...],
        winning_operation_id: str | None,
    ) -> None:
        details: dict[str, object] = {"operation_id": str(operation[0])}
        if winning_operation_id is not None:
            details["winning_operation_id"] = winning_operation_id
        error = {
            "error": "effect_evidence_conflict",
            "message": "Persisted effect evidence contradicts the claimed task operation",
            "details": details,
        }
        cursor.execute(
            "UPDATE board_task_operations SET operation_state='blocked', "
            "effect_state='conflict', error_json=? "
            "WHERE operation_id=? AND operation_state='claimed' AND effect_state='pending'",
            (_json_object(error), str(operation[0])),
        )
        if cursor.rowcount != 1:
            raise _service_error(
                "operation_state_conflict", "Task operation changed during finalization", 409, {}
            )
        if str(operation[15]) in {"bank_release", "bank_split"}:
            failed_at_us = self._sample_clock()
            cursor.execute(
                "UPDATE board_task_effect_deliveries SET delivery_state='integrity_blocked',"
                "next_attempt_at_us=NULL,lease_owner_id=NULL,lease_acquired_at_us=NULL,"
                "lease_expires_at_us=NULL,last_failure_code='effect_evidence_conflict',"
                "last_failure_at_us=?,next_evidence_check_at_us=NULL,completed_at_us=NULL,"
                "completion_event_id=NULL WHERE native_operation_id=?",
                (failed_at_us, str(operation[0])),
            )
            if cursor.rowcount != 1:
                raise _service_error(
                    "effect_delivery_missing",
                    "Native financial operation has no delivery sidecar",
                    500,
                    {"operation_id": str(operation[0])},
                )
        self._db.commit()
        raise _service_error(
            str(error["error"]),
            str(error["message"]),
            409,
            details,
        )

    @staticmethod
    def _validate_claim_ownership(
        operation: tuple[Any, ...],
        task: dict[str, Any],
        contract: dict[str, Any],
    ) -> None:
        if task["status"] != operation[9] or contract["lifecycle_version"] != operation[10]:
            raise _service_error(
                "operation_claim_lost",
                "Task no longer matches the operation's owned source state",
                409,
                {"operation_id": str(operation[0])},
            )

    def _complete_bank_effect(
        self,
        cursor: sqlite3.Cursor,
        operation: tuple[Any, ...],
        task: dict[str, Any],
        contract: dict[str, Any],
        completed_at_us: int,
    ) -> dict[str, Any]:
        command = str(operation[3])
        target_status = str(operation[11])
        target_version = int(operation[12])
        timestamp_column = {
            "cancel_task": "cancelled_at",
            "expire_bidding": "expired_at",
            "expire_acceptance": "expired_at",
            "expire_execution": "expired_at",
            "approve_task": "approved_at",
            "auto_approve": "approved_at",
            "record_ruling": "ruled_at",
        }[command]
        timestamp = epoch_us_to_utc_text(completed_at_us)
        event_request = self._request_from_operation(operation)
        if command == "record_ruling":
            cursor.execute(
                "UPDATE board_tasks SET status=?, ruled_at=?, ruling_id=?, worker_pct=?, "
                "ruling_summary=?, escrow_pending=0 WHERE task_id=? AND status=?",
                (
                    target_status,
                    timestamp,
                    event_request.argument("ruling_id"),
                    event_request.argument("worker_pct"),
                    event_request.argument("summary"),
                    str(operation[2]),
                    str(operation[9]),
                ),
            )
        else:
            cursor.execute(
                f"UPDATE board_tasks SET status=?, {timestamp_column}=?, escrow_pending=0 "  # nosec B608
                "WHERE task_id=? AND status=?",
                (target_status, timestamp, str(operation[2]), str(operation[9])),
            )
        if cursor.rowcount != 1:
            raise _service_error("operation_claim_lost", "Task operation claim was lost", 409, {})
        cursor.execute(
            "UPDATE board_task_contracts SET lifecycle_version=? "
            "WHERE task_id=? AND lifecycle_version=?",
            (target_version, str(operation[2]), int(operation[10])),
        )
        event_id = self._insert_event(cursor, event_request, completed_at_us)
        result: dict[str, object] = {
            "task_id": str(operation[2]),
            "command": command,
            "status": target_status,
            "lifecycle_version": target_version,
            "event_id": event_id,
            "effect_result_reference": str(operation[0]),
        }
        if task["worker_id"] is not None:
            result["worker_id"] = str(task["worker_id"])
        if command == "record_ruling":
            result["ruling_id"] = str(event_request.argument("ruling_id"))
            result["worker_pct"] = int(event_request.argument("worker_pct"))
            result["summary"] = str(event_request.argument("summary"))
        accepted_reward = contract["accepted_reward"]
        escrow_amount = (
            int(accepted_reward) if isinstance(accepted_reward, int) else int(task["reward"])
        )
        result["reward"] = escrow_amount
        if command in {"approve_task", "auto_approve", "record_ruling"}:
            worker_payment = self._settlement_worker_amount(event_request, contract)
            result["accepted_price"] = int(contract["accepted_bid_amount"])
            result["worker_payment"] = worker_payment
            result["poster_refund"] = escrow_amount - worker_payment
        cursor.execute(
            "UPDATE board_task_operations SET operation_state='completed', "
            "effect_state='succeeded', effect_result_reference=?, completed_at_us=?, "
            "event_id=?, result_json=? WHERE operation_id=? AND operation_state='claimed'",
            (
                str(operation[0]),
                completed_at_us,
                event_id,
                _json_object(result),
                str(operation[0]),
            ),
        )
        refreshed = self._operation_row(cursor, str(operation[0]))
        if refreshed is None:
            raise _service_error(
                "operation_state_corrupt", "Completed operation disappeared", 500, {}
            )
        return self._envelope(refreshed)

    @staticmethod
    def _request_from_operation(operation: tuple[Any, ...]) -> TaskOperationRequest:
        from db_gateway_service.task_operation_requests import parse_task_operation_request

        decoded: object = json.loads(str(operation[4]))
        if not isinstance(decoded, dict):
            raise _service_error("operation_state_corrupt", "Stored request is invalid", 500, {})
        payload = dict(decoded)
        payload["operation_id"] = str(operation[0])
        return parse_task_operation_request(payload)

    @staticmethod
    def _envelope(operation: tuple[Any, ...]) -> dict[str, Any]:
        effect_intent = None if operation[19] is None else json.loads(str(operation[19]))
        result = None if operation[22] is None else json.loads(str(operation[22]))
        stored_request: object = json.loads(str(operation[4]))
        if (
            not isinstance(stored_request, dict)
            or type(stored_request.get("protocol_version")) is not int
        ):
            raise _service_error(
                "operation_state_corrupt", "Stored protocol version is invalid", 500, {}
            )
        protocol_version = int(stored_request["protocol_version"])
        if protocol_version not in {1, 2}:
            raise _service_error(
                "operation_state_corrupt", "Stored protocol version is invalid", 500, {}
            )
        return {
            "protocol_version": protocol_version,
            "operation_id": str(operation[0]),
            "task_id": str(operation[2]),
            "command": str(operation[3]),
            "request_sha256": str(operation[5]),
            "operation_state": str(operation[14]),
            "source_status": str(operation[9]),
            "source_lifecycle_version": int(operation[10]),
            "target_status": str(operation[11]),
            "target_lifecycle_version": int(operation[12]),
            "effect_kind": str(operation[15]),
            "effect_state": str(operation[16]),
            "effect_operation_id": None if operation[17] is None else str(operation[17]),
            "effect_result_reference": None if operation[18] is None else str(operation[18]),
            "effect_intent": effect_intent,
            "event_id": None if operation[21] is None else int(operation[21]),
            "result": result,
        }


__all__ = ["TaskOperationWriter"]

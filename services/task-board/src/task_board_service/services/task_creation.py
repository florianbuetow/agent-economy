"""Task creation and pre-bidding lifecycle (WP-11, B16 god-class decomposition).

Extracted from task_manager.py. Reads its dependencies through the owning
TaskManager facade (``self._manager``) rather than snapshotting them at
construction — tests reach into ``task_manager.store`` etc. and reassign it
post-construction (see routers/conftest.py), and a snapshot copy would go
stale the moment that happens. Router surfaces are byte-stable: TaskManager's
public methods still exist with identical signatures and just delegate here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from service_auth.financial_identity import operation_id_for
from service_auth.jws import parse_jws
from service_commons.exceptions import ServiceError

from task_board_service.services.errors import DuplicateTaskError
from task_board_service.services.task_helpers import (
    TASK_ID_RE,
    is_positive_int,
    now_iso,
    task_to_response,
    task_to_summary,
)
from task_board_service.services.task_operations import (
    complete_bank_operation,
    operation_response,
    public_operation_identity,
)
from task_board_service.task_operation_contract import MAX_INTEGER

if TYPE_CHECKING:
    from task_board_service.services.task_manager import TaskManager

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECONDS_PER_SECOND = 1_000_000


def _invalid_acceptance_window() -> ServiceError:
    return ServiceError(
        "invalid_deadline",
        "acceptance_window_seconds must produce a representable cutoff",
        400,
        {},
    )


def _validate_acceptance_cutoff(
    created_at: str,
    bidding_deadline_seconds: int,
    acceptance_window_seconds: int,
) -> None:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if created.tzinfo is None or created.utcoffset() != UTC.utcoffset(created):
            raise ValueError("created_at must use UTC")
        delta = created - _EPOCH
        created_us = (
            delta.days * 86_400 * _MICROSECONDS_PER_SECOND
            + delta.seconds * _MICROSECONDS_PER_SECOND
            + delta.microseconds
        )
        duration_seconds = bidding_deadline_seconds + acceptance_window_seconds
        if (
            created_us < 0
            or duration_seconds > (MAX_INTEGER - created_us) // _MICROSECONDS_PER_SECOND
        ):
            raise OverflowError("cutoff exceeds the SQLite integer range")
        _ = created + timedelta(seconds=duration_seconds)
    except (OverflowError, ValueError):
        raise _invalid_acceptance_window() from None


def _invalid_lock_response() -> ServiceError:
    return ServiceError(
        "central_bank_unavailable",
        "Central Bank returned an invalid escrow lock receipt",
        502,
        {},
    )


def new_lock_escrow_id(
    envelope: dict[str, Any],
    *,
    operation_id: str,
    task_id: str,
    poster_id: str,
    reward: int,
) -> str:
    """Return the escrow ID only for a matching, currently locked native receipt."""
    receipt = envelope.get("receipt")
    current = envelope.get("current_escrow")
    if envelope.get("outcome") != "native_receipt":
        raise _invalid_lock_response()
    if not isinstance(receipt, dict) or not isinstance(current, dict):
        raise _invalid_lock_response()
    result = receipt.get("result")
    legs = receipt.get("legs")
    if not isinstance(result, dict) or not isinstance(legs, list) or len(legs) != 1:
        raise _invalid_lock_response()
    leg = legs[0]
    if not isinstance(leg, dict):
        raise _invalid_lock_response()
    escrow_id = receipt.get("escrow_id")
    if not isinstance(escrow_id, str) or escrow_id == "":
        raise _invalid_lock_response()
    valid_receipt = (
        receipt.get("receipt_version") == 1
        and receipt.get("operation_id") == operation_id
        and receipt.get("operation_kind") == "escrow_lock"
        and receipt.get("source_reference") == task_id
        and result.get("escrow_status_at_commit") == "locked"
        and leg.get("role") == "payer"
        and leg.get("account_id") == poster_id
        and leg.get("direction") == "debit"
        and leg.get("amount") == reward
        and not isinstance(leg.get("amount"), bool)
    )
    valid_current = (
        current.get("escrow_id") == escrow_id
        and current.get("payer_account_id") == poster_id
        and current.get("task_id") == task_id
        and current.get("amount") == reward
        and not isinstance(current.get("amount"), bool)
        and current.get("status") == "locked"
        and current.get("resolved_at") is None
    )
    if not valid_receipt or not valid_current:
        raise _invalid_lock_response()
    return escrow_id


class TaskCreationCoordinator:
    """Handles task creation, lookup, listing, and poster-initiated cancellation."""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    async def create_task(self, task_token: str, escrow_token: str) -> dict[str, Any]:
        """
        Create a new task with escrow.

        Error precedence:
        1. invalid_jws — malformed task_token (via _validate_jws_token)
        2. identity_service_unavailable — Identity unreachable
        3. forbidden — invalid signature or signer mismatch (payload-level)
        4. invalid_payload — wrong action, missing fields, invalid values
        5. token_mismatch — cross-token validation
        6. task_already_exists — duplicate task_id
        7. central_bank_unavailable / insufficient_funds — escrow lock
        """
        manager = self._manager
        # Steps 4-7a: Verify task_token via Identity service, validate action
        payload = await manager.token_validator.validate_jws_token(
            task_token,
            "create_task",
            expected_resource="POST /tasks",
        )
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields in task_token payload
        required_fields = [
            "task_id",
            "poster_id",
            "title",
            "spec",
            "reward",
            "bidding_deadline_seconds",
            "acceptance_window_seconds",
            "review_deadline_seconds",
        ]
        for field_name in required_fields:
            if field_name not in payload:
                raise ServiceError(
                    "invalid_payload",
                    f"Missing required field: {field_name}",
                    400,
                    {},
                )

        if "deadline_seconds" in payload:
            deadline_seconds_field = "deadline_seconds"
        elif "execution_deadline_seconds" in payload:
            deadline_seconds_field = "execution_deadline_seconds"
        else:
            raise ServiceError(
                "invalid_payload",
                "Missing required field: execution_deadline_seconds",
                400,
                {},
            )

        # Step 9a: Signer must match poster_id in payload (payload-level check)
        poster_id: str = payload["poster_id"]
        if signer_id != poster_id:
            raise ServiceError(
                "forbidden",
                "Signer does not match poster_id",
                403,
                {},
            )

        task_id_obj: object = payload["task_id"]
        title_obj: object = payload["title"]
        spec_obj: object = payload["spec"]
        reward: object = payload["reward"]
        bidding_deadline_seconds: object = payload["bidding_deadline_seconds"]
        acceptance_window_seconds: object = payload["acceptance_window_seconds"]
        deadline_seconds: object = payload[deadline_seconds_field]
        review_deadline_seconds: object = payload["review_deadline_seconds"]

        # Step 7c: Validate task_id format
        if not isinstance(task_id_obj, str) or not TASK_ID_RE.match(task_id_obj):
            raise ServiceError(
                "invalid_task_id",
                "task_id must match the format t-<uuid4>",
                400,
                {},
            )
        task_id = task_id_obj

        # Step 7d: Validate title
        if not isinstance(title_obj, str) or len(title_obj) < 1:
            raise ServiceError(
                "invalid_payload",
                "Title must be a non-empty string",
                400,
                {},
            )
        # T-037 (exception #9): the custom 'title_too_long' code contradicted
        # test spec TC-15 (task-board-service-tests.md), which expects the
        # generic invalid_payload code here — the message stays specific.
        if len(title_obj) > 200:
            raise ServiceError(
                "invalid_payload",
                "Title must not exceed 200 characters",
                400,
                {},
            )
        title = title_obj

        # Step 7e: Validate spec
        if not isinstance(spec_obj, str) or len(spec_obj) < 1 or len(spec_obj) > 10000:
            raise ServiceError(
                "invalid_payload",
                "Spec must be between 1 and 10,000 characters",
                400,
                {},
            )
        spec = spec_obj

        # Step 7f: Validate reward (must be positive integer, not float, not bool)
        if not is_positive_int(reward):
            raise ServiceError(
                "invalid_reward",
                "Reward must be a positive integer",
                400,
                {},
            )

        # Step 7g: Validate deadlines (each must be a positive integer)
        for dl_name, dl_value in [
            ("bidding_deadline_seconds", bidding_deadline_seconds),
            ("deadline_seconds", deadline_seconds),
            ("review_deadline_seconds", review_deadline_seconds),
        ]:
            if not is_positive_int(dl_value):
                raise ServiceError(
                    "invalid_deadline",
                    f"{dl_name} must be a positive integer",
                    400,
                    {},
                )
        if not is_positive_int(acceptance_window_seconds):
            raise ServiceError(
                "invalid_deadline",
                "acceptance_window_seconds must be a positive representable integer",
                400,
                {},
            )
        acceptance_window_seconds_int = cast("int", acceptance_window_seconds)

        reward_int = cast("int", reward)
        bidding_deadline_seconds_int = cast("int", bidding_deadline_seconds)
        deadline_seconds_int = cast("int", deadline_seconds)
        review_deadline_seconds_int = cast("int", review_deadline_seconds)
        created_at = now_iso()
        _validate_acceptance_cutoff(
            created_at,
            bidding_deadline_seconds_int,
            acceptance_window_seconds_int,
        )

        # Step 8: Cross-validate escrow_token payload (decoded without sig verification)
        escrow_payload = manager.token_validator.decode_escrow_token_payload(escrow_token)
        escrow_header = parse_jws(escrow_token).header

        escrow_task_id = escrow_payload.get("task_id")
        escrow_amount = escrow_payload.get("amount")
        escrow_operation_id = escrow_payload.get("operation_id")

        # Missing fields in escrow payload means cross-validation cannot proceed
        if escrow_task_id is None or escrow_amount is None or escrow_operation_id is None:
            raise ServiceError(
                "token_mismatch",
                "Escrow token payload must include task_id, amount, and operation_id",
                400,
                {},
            )

        if escrow_task_id != task_id:
            raise ServiceError(
                "token_mismatch",
                "task_id mismatch between task_token and escrow_token",
                400,
                {},
            )

        if escrow_amount != reward_int:
            raise ServiceError(
                "token_mismatch",
                "reward/amount mismatch between task_token and escrow_token",
                400,
                {},
            )

        expected_operation_id = operation_id_for("task-lock", (task_id, poster_id))
        if escrow_operation_id != expected_operation_id:
            raise ServiceError(
                "token_mismatch",
                "escrow operation_id does not match the task posting intent",
                400,
                {},
            )

        escrow_signer_id = escrow_header.kid
        if escrow_signer_id != signer_id:
            raise ServiceError(
                "token_mismatch",
                "escrow signer does not match task signer",
                400,
                {},
            )

        escrow_agent_id = escrow_payload.get("agent_id")
        if isinstance(escrow_agent_id, str) and escrow_agent_id != poster_id:
            raise ServiceError(
                "token_mismatch",
                "escrow signer agent_id does not match poster_id",
                400,
                {},
            )

        # Step 10 (variant): Check task_id not already in DB
        existing = manager.store.get_task(task_id)
        if existing is not None:
            raise ServiceError(
                "task_already_exists",
                f"A task with task_id '{task_id}' already exists",
                409,
                {},
            )

        # Step 13: Lock escrow via Central Bank (forwards the poster's escrow_token)
        # BankClient.lock_escrow raises:
        #   ServiceError("central_bank_unavailable", ..., 502) on connection/timeout
        #   ServiceError("insufficient_funds", ..., 402) when CB reports insufficient funds
        try:
            escrow_result = await manager.central_bank_client.escrow_lock(escrow_token)
        except ServiceError:
            raise
        except Exception as exc:
            error_text = str(exc).lower()
            if "insufficient_funds" in error_text:
                raise ServiceError(
                    "insufficient_funds",
                    "Poster has insufficient funds to cover the task reward",
                    402,
                    {},
                ) from exc
            raise ServiceError(
                "central_bank_unavailable",
                "Cannot connect to Central Bank",
                502,
                {},
            ) from exc
        escrow_id = new_lock_escrow_id(
            escrow_result,
            operation_id=expected_operation_id,
            task_id=task_id,
            poster_id=poster_id,
            reward=reward_int,
        )

        # Insert task into DB
        try:
            manager.store.insert_task(
                {
                    "task_id": task_id,
                    "poster_id": poster_id,
                    "title": title,
                    "spec": spec,
                    "reward": reward_int,
                    "bidding_deadline_seconds": bidding_deadline_seconds_int,
                    "acceptance_window_seconds": acceptance_window_seconds_int,
                    "deadline_seconds": deadline_seconds_int,
                    "review_deadline_seconds": review_deadline_seconds_int,
                    "status": "open",
                    "escrow_id": escrow_id,
                    "bid_count": 0,
                    "worker_id": None,
                    "accepted_bid_id": None,
                    "created_at": created_at,
                    "accepted_at": None,
                    "submitted_at": None,
                    "approved_at": None,
                    "cancelled_at": None,
                    "disputed_at": None,
                    "dispute_reason": None,
                    "dispute_id": None,
                    "rebuttal_deadline": None,
                    "rebuttal_submitted_at": None,
                    "ruling_id": None,
                    "ruled_at": None,
                    "worker_pct": None,
                    "ruling_summary": None,
                    "expired_at": None,
                    "escrow_pending": 0,
                }
            )
        except DuplicateTaskError as exc:
            # DB insert failed (e.g., race condition on duplicate task_id)
            # Rollback escrow: release back to poster
            try:
                await manager.escrow_coordinator.release_posting_compensation(
                    task_id,
                    escrow_id,
                    poster_id,
                )
            except ServiceError:
                manager.logger.error(
                    "Failed to release escrow during rollback",
                    extra={"task_id": task_id, "escrow_id": escrow_id},
                )
            raise ServiceError(
                "task_already_exists",
                f"A task with task_id '{task_id}' already exists",
                409,
                {},
            ) from exc

        task = manager.store.get_task(task_id)
        if task is None:
            msg = f"Task {task_id} not found after insert"
            raise RuntimeError(msg)
        return task_to_response(task)

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """
        Get a single task by ID with deadline evaluation.

        Raises:
            ServiceError: task_not_found
        """
        manager = self._manager
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})
        task = await manager.deadline_evaluator.evaluate_deadline(task)
        return task_to_response(task)

    async def list_tasks(
        self,
        status: str | None,
        poster_id: str | None,
        worker_id: str | None,
        offset: int | None,
        limit: int | None,
    ) -> list[dict[str, Any]]:
        """
        List tasks with optional filters. All filters use AND logic.

        Returns a list of task summary dicts.
        """
        manager = self._manager
        tasks = manager.store.list_tasks(
            status=status,
            poster_id=poster_id,
            worker_id=worker_id,
            limit=limit,
            offset=offset,
        )

        # Evaluate deadlines for all tasks
        tasks = await manager.deadline_evaluator.evaluate_deadlines_batch(tasks)

        return [task_to_summary(t) for t in tasks]

    async def cancel_task(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Cancel a task and release escrow to the poster.

        Error precedence:
        1-6. JWS verification (via _validate_jws_token)
        7.   invalid_payload — wrong action, missing fields, task_id mismatch
        9a.  forbidden — signer != poster_id in payload
        10.  task_not_found
        11.  invalid_status — not OPEN
        9b.  forbidden — signer != task's poster
        13.  central_bank_unavailable
        """
        manager = self._manager
        # Steps 4-7a: Verify JWS, validate action
        payload = await manager.token_validator.validate_jws_token(
            token,
            "cancel_task",
            expected_resource=f"POST /tasks/{task_id}/cancel",
        )
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate task_id in payload
        if "task_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: task_id", 400, {})

        if "poster_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: poster_id", 400, {})

        operation_id, expected_version = public_operation_identity(payload)

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "invalid_payload",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: Signer must match poster_id in payload
        if signer_id != payload["poster_id"]:
            raise ServiceError("forbidden", "Signer does not match poster_id", 403, {})

        # Step 10: Load task
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        # Gateway rechecks actual bid existence if a bid races this strict read.
        has_bids = bool(manager.store.get_bids_for_task(task_id))
        cutoff = task["acceptance_cutoff_us"] if has_bids else task["bidding_cutoff_us"]
        envelope = manager.store.claim_task_operation(
            {
                "protocol_version": 1,
                "operation_id": operation_id,
                "task_id": task_id,
                "command": "cancel_task",
                "expected_status": "open",
                "expected_lifecycle_version": expected_version,
                "actor_kind": "agent",
                "actor_id": signer_id,
                "arguments": {
                    "poster_id": task["poster_id"],
                    "escrow_id": task["escrow_id"],
                    "reward": task["reward"],
                    "source_reference": f"task:{task_id}:cancelled",
                    "cutoff_us": cutoff,
                },
            }
        )
        finalized = await complete_bank_operation(
            manager.store, manager.escrow_coordinator, envelope
        )
        return operation_response(manager.store, finalized)

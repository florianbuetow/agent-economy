"""Task creation and pre-bidding lifecycle (WP-11, B16 god-class decomposition).

Extracted from task_manager.py. Reads its dependencies through the owning
TaskManager facade (``self._manager``) rather than snapshotting them at
construction — tests reach into ``task_manager.store`` etc. and reassign it
post-construction (see routers/conftest.py), and a snapshot copy would go
stale the moment that happens. TaskManager delegates the native publication
and established lifecycle surfaces here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_auth.financial_responses import SQLITE_MAX_INTEGER
from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse
from service_commons.exceptions import ServiceError

from task_board_service.services.task_helpers import task_to_response, task_to_summary
from task_board_service.services.task_operations import (
    complete_bank_operation,
    operation_response,
    public_operation_identity,
)
from task_board_service.task_operation_contract import validate_operation_id

if TYPE_CHECKING:
    from task_board_service.services.task_manager import TaskManager

_TASK_FIELDS = frozenset(
    {
        "action",
        "protocol_version",
        "operation_id",
        "task_id",
        "poster_id",
        "lock_operation_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
        "_signer_id",
    }
)
_ESCROW_FIELDS = frozenset(
    {"action", "operation_id", "task_id", "agent_id", "amount", "_signer_id"}
)


def _invalid_posting(exc: Exception) -> ServiceError:
    message = str(exc)
    if "reward" in message:
        return ServiceError("invalid_reward", "Reward must be a positive integer", 400, {})
    if "deadline" in message or "duration" in message or "cutoff" in message:
        return ServiceError("invalid_deadline", "Task deadlines are invalid", 400, {})
    if "task_id" in message:
        return ServiceError("invalid_task_id", "task_id must match the format t-<uuid>", 400, {})
    return ServiceError("invalid_payload", "Invalid task posting intent", 400, {})


def _intent_from_payload(payload: dict[str, Any]) -> TaskPostingIntent:
    if frozenset(payload) != _TASK_FIELDS:
        raise ServiceError(
            "invalid_payload",
            "Task token must contain exactly the required fields",
            400,
            {},
        )
    signer_id = payload["_signer_id"]
    poster_id = payload["poster_id"]
    if not isinstance(signer_id, str) or signer_id != poster_id:
        raise ServiceError("forbidden", "Signer does not match poster_id", 403, {})
    for field in (
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
    ):
        duration = payload[field]
        if type(duration) is not int or not 1 <= duration <= SQLITE_MAX_INTEGER // 1_000_000:
            raise ServiceError(
                "invalid_deadline",
                f"{field} must be a positive integer in the supported range",
                400,
                {},
            )
    try:
        return TaskPostingIntent.from_json(
            {
                "protocol_version": payload["protocol_version"],
                "operation_id": payload["operation_id"],
                "task_id": payload["task_id"],
                "actor_kind": "agent",
                "actor_id": poster_id,
                "lock_operation_id": payload["lock_operation_id"],
                "title": payload["title"],
                "spec": payload["spec"],
                "reward": payload["reward"],
                "bidding_deadline_seconds": payload["bidding_deadline_seconds"],
                "acceptance_window_seconds": payload["acceptance_window_seconds"],
                "execution_deadline_seconds": payload["execution_deadline_seconds"],
                "review_deadline_seconds": payload["review_deadline_seconds"],
            }
        )
    except (TypeError, ValueError) as exc:
        raise _invalid_posting(exc) from exc


def _validate_escrow_payload(
    payload: dict[str, Any],
    intent: TaskPostingIntent,
) -> None:
    if frozenset(payload) != _ESCROW_FIELDS:
        raise ServiceError(
            "invalid_payload",
            "Escrow token must contain exactly the required fields",
            400,
            {},
        )
    if payload["_signer_id"] != intent.actor_id:
        raise ServiceError("token_mismatch", "Escrow signer does not match task signer", 400, {})
    expected = {
        "action": "escrow_lock",
        "operation_id": intent.lock_operation_id,
        "task_id": intent.task_id,
        "agent_id": intent.actor_id,
        "amount": intent.reward,
    }
    if any(
        type(payload[name]) is not type(value) or payload[name] != value
        for name, value in expected.items()
    ):
        raise ServiceError(
            "token_mismatch",
            "Escrow token does not match the task posting intent",
            400,
            {},
        )


def _raise_blocked(response: TaskPostingResponse) -> None:
    body = response.body
    details = body.get("details")
    if (
        response.status_code != 409
        or body.get("error") != "publication_integrity_blocked"
        or body.get("message") != "Publication evidence requires reconciliation"
        or not isinstance(details, dict)
    ):
        raise RuntimeError("Invalid blocked task publication response")
    raise ServiceError(
        "publication_integrity_blocked",
        "Publication evidence requires reconciliation",
        409,
        details,
    )


class TaskCreationCoordinator:
    """Handles task creation, lookup, listing, and poster-initiated cancellation."""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    async def create_task(
        self,
        task_token: str,
        escrow_token: str,
    ) -> TaskPostingResponse:
        """Admit, fund, and reconcile one immutable native task publication."""
        manager = self._manager
        task_payload = await manager.token_validator.validate_jws_token(
            task_token,
            "create_task",
            expected_resource="POST /tasks",
        )
        intent = _intent_from_payload(task_payload)
        escrow_payload = await manager.token_validator.validate_escrow_jws_token(escrow_token)
        _validate_escrow_payload(escrow_payload, intent)

        admitted = manager.store.admit_task_publication(intent)
        if admitted.status_code == 409:
            _raise_blocked(admitted)
        if admitted.body["publication_state"] == "published":
            return admitted

        reconciled = manager.store.reconcile_task_publication(intent.operation_id, intent)
        if reconciled.status_code == 409:
            _raise_blocked(reconciled)
        if reconciled.body["publication_state"] == "published":
            return reconciled

        try:
            await manager.central_bank_client.escrow_lock(escrow_token)
        except ServiceError as bank_error:
            after_error = manager.store.reconcile_task_publication(intent.operation_id, intent)
            if after_error.status_code == 409:
                _raise_blocked(after_error)
            if after_error.body["publication_state"] == "published":
                return after_error
            if bank_error.status_code < 500:
                raise bank_error
            return after_error

        published = manager.store.reconcile_task_publication(intent.operation_id, intent)
        if published.status_code == 409:
            _raise_blocked(published)
        return published

    async def get_task_publication(
        self,
        operation_id: str,
        token: str,
    ) -> TaskPostingResponse:
        """Return one private publication state for its freshly verified owner."""
        validated_operation_id = validate_operation_id(operation_id)
        payload = await self._manager.token_validator.validate_jws_token(
            token,
            "get_task_publication",
            expected_resource=f"GET /task-publications/{validated_operation_id}",
        )
        expected_fields = frozenset({"action", "operation_id", "poster_id", "_signer_id"})
        if (
            frozenset(payload) != expected_fields
            or payload["operation_id"] != validated_operation_id
        ):
            raise ServiceError(
                "invalid_payload",
                "Task publication status token is invalid",
                400,
                {},
            )
        poster_id = payload["poster_id"]
        if not isinstance(poster_id, str) or payload["_signer_id"] != poster_id:
            raise ServiceError("forbidden", "Signer does not match poster_id", 403, {})
        return self._manager.store.get_task_publication(validated_operation_id, poster_id)

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

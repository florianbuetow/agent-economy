"""Deliverable submission and poster review (WP-11, B16 god-class decomposition).

Extracted from task_manager.py. See task_creation.py's module docstring for
why coordinators read dependencies through ``self._manager`` rather than a
constructor-time snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError
from service_commons.telemetry import annotate

from task_board_service.services.task_operations import (
    accepted_task_arguments,
    complete_bank_operation,
    operation_response,
    public_operation_identity,
)

if TYPE_CHECKING:
    from task_board_service.services.task_manager import TaskManager


class TaskReviewCoordinator:
    """Handles deliverable submission and poster approval."""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    async def submit_deliverable(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Submit deliverables for review.

        Error precedence:
        1-6. JWS verification
        7.   invalid_payload — wrong action, task/operation/version fields
        9a.  forbidden — signer != worker_id in payload
        10+. Gateway claim — task/status/version/worker/asset/cutoff predicates
        """
        annotate(task_id=task_id, action="submit_deliverable")
        manager = self._manager
        # Steps 4-7a: Verify JWS, validate action
        payload = await manager.token_validator.validate_jws_token(
            token,
            "submit_deliverable",
            expected_resource=f"POST /tasks/{task_id}/submit",
        )
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields and caller-owned replay identity.
        if "task_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: task_id", 400, {})

        if "worker_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: worker_id", 400, {})
        operation_id, expected_version = public_operation_identity(payload)

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "invalid_payload",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: Signer must match worker_id in payload
        if signer_id != payload["worker_id"]:
            raise ServiceError("forbidden", "Signer does not match worker_id", 403, {})

        # ponytail: protocol 1 remains only for tasks without an artifact contract; drop
        # it once every accepted task is governed (all posting is protocol 2).
        # Governed (V6) tasks submit with protocol 2: the Gateway freezes the exact
        # ready-upload manifest bound to the accepted terms in the same transaction.
        governed_arguments = manager.asset_manager.submission_arguments(task_id, signer_id)
        envelope = manager.store.claim_task_operation(
            {
                "protocol_version": 1 if governed_arguments is None else 2,
                "operation_id": operation_id,
                "task_id": task_id,
                "command": "submit_deliverable",
                "expected_status": "accepted",
                "expected_lifecycle_version": expected_version,
                "actor_kind": "agent",
                "actor_id": signer_id,
                "arguments": (
                    {"worker_id": signer_id} if governed_arguments is None else governed_arguments
                ),
            }
        )
        return operation_response(manager.store, envelope)

    async def approve_task(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Approve deliverables and release escrow to the worker.

        Error precedence:
        1-6. JWS verification
        7.   invalid_payload — wrong action, task/operation/version fields
        9a.  forbidden — signer != poster_id in payload
        10.  task_not_found
        11+. Gateway claim — status/version/actor/frozen-term predicates
        13.  persisted Bank-effect delivery/finalization
        """
        annotate(task_id=task_id, action="approve_task")
        manager = self._manager
        # Steps 4-7a: Verify JWS, validate action
        payload = await manager.token_validator.validate_jws_token(
            token,
            "approve_task",
            expected_resource=f"POST /tasks/{task_id}/approve",
        )
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields and caller-owned replay identity.
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

        # Load immutable command facts. Gateway owns mutable eligibility and actor entitlement.
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})
        arguments = accepted_task_arguments(task, expected_version, "submitted")
        arguments["source_reference"] = f"task:{task_id}:approved"

        envelope = manager.store.claim_task_operation(
            {
                "protocol_version": 1,
                "operation_id": operation_id,
                "task_id": task_id,
                "command": "approve_task",
                "expected_status": "submitted",
                "expected_lifecycle_version": expected_version,
                "actor_kind": "agent",
                "actor_id": signer_id,
                "arguments": arguments,
            }
        )
        completed = await complete_bank_operation(
            manager.store,
            manager.escrow_coordinator,
            envelope,
        )
        return operation_response(manager.store, completed)

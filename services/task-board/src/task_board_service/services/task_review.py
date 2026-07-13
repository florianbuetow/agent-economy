"""Deliverable submission and poster review (WP-11, B16 god-class decomposition).

Extracted from task_manager.py. See task_creation.py's module docstring for
why coordinators read dependencies through ``self._manager`` rather than a
constructor-time snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from task_board_service.services.task_helpers import now_iso, task_to_response

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
        7.   invalid_payload — wrong action, task_id mismatch
        9a.  forbidden — signer != worker_id in payload
        10.  task_not_found
        11.  invalid_status — not ACCEPTED
        9b.  forbidden — signer != task's worker_id
        12.  no_assets — no assets uploaded
        """
        manager = self._manager
        # Steps 4-7a: Verify JWS, validate action
        payload = await manager.token_validator.validate_jws_token(token, "submit_deliverable")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: task_id", 400, {})

        if "worker_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: worker_id", 400, {})

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

        # Step 10: Load task
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        # Evaluate deadline
        task = await manager.deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "accepted":
            raise ServiceError(
                "invalid_status",
                (
                    "Cannot submit deliverable for task in "
                    f"'{task['status']}' status, must be 'accepted'"
                ),
                409,
                {},
            )

        # Step 9b: Signer must be the task's assigned worker
        if signer_id != task["worker_id"]:
            raise ServiceError(
                "forbidden",
                "Only the assigned worker can submit deliverables",
                403,
                {},
            )

        # Step 12: At least one asset must exist
        asset_count = manager.asset_manager.count_assets(task_id)
        if asset_count == 0:
            raise ServiceError(
                "no_assets",
                "At least one asset must be uploaded before submitting",
                400,
                {},
            )

        # Update task
        submitted_at = now_iso()
        manager.store.update_task(
            task_id,
            {"status": "submitted", "submitted_at": submitted_at},
            expected_status=None,
        )

        updated = manager.store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return task_to_response(updated)

    async def approve_task(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Approve deliverables and release escrow to the worker.

        Error precedence:
        1-6. JWS verification
        7.   invalid_payload — wrong action, task_id mismatch
        9a.  forbidden — signer != poster_id in payload
        10.  task_not_found
        11.  invalid_status — not SUBMITTED
        9b.  forbidden — signer != task's poster
        13.  central_bank_unavailable
        """
        manager = self._manager
        # Steps 4-7a: Verify JWS, validate action
        payload = await manager.token_validator.validate_jws_token(token, "approve_task")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: task_id", 400, {})

        if "poster_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: poster_id", 400, {})

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

        # Evaluate deadline
        task = await manager.deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status
        if task["status"] != "submitted":
            raise ServiceError(
                "invalid_status",
                f"Cannot approve task in '{task['status']}' status, must be 'submitted'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's poster
        if signer_id != task["poster_id"]:
            raise ServiceError("forbidden", "Only the poster can approve", 403, {})

        # Step 13: Release escrow to worker
        await manager.escrow_coordinator.release_escrow(task["escrow_id"], task["worker_id"])

        # Update task
        approved_at = now_iso()
        manager.store.update_task(
            task_id,
            {"status": "approved", "approved_at": approved_at},
            expected_status=None,
        )

        updated = manager.store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return task_to_response(updated)

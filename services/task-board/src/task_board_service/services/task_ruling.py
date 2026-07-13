"""Dispute, rebuttal, and Court ruling recording (WP-11, B16 god-class decomposition).

Extracted from task_manager.py. See task_creation.py's module docstring for
why coordinators read dependencies through ``self._manager`` rather than a
constructor-time snapshot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import PlatformHttpError
from task_board_service.services.task_helpers import is_valid_worker_pct, now_iso, task_to_response

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent

    from task_board_service.services.task_manager import TaskManager


class TaskRulingCoordinator:
    """Handles dispute filing, rebuttal submission, and Court ruling recording."""

    def __init__(self, manager: TaskManager) -> None:
        self._manager = manager

    async def dispute_task(
        self,
        task_id: str,
        token: str,
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """
        Dispute deliverables — sends task to the Court for resolution.

        Error precedence:
        1-6. JWS verification
        7.   invalid_payload — wrong action, task_id mismatch
        9a.  forbidden — signer != poster_id in payload
        10.  task_not_found
        11.  invalid_status — not SUBMITTED
        9b.  forbidden — signer != task's poster
        12.  invalid_reason — empty or too long
        13.  court_unavailable — Court returned no dispute id
        """
        manager = self._manager
        # Steps 4-7a: Verify JWS, validate action. T-036 (exception #8): the
        # undocumented 'file_dispute' alias is removed — dispute_task is the
        # only accepted action.
        payload = await manager.token_validator.validate_jws_token(
            token,
            "dispute_task",
        )
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
                f"Cannot dispute task in '{task['status']}' status, must be 'submitted'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's poster
        if signer_id != task["poster_id"]:
            raise ServiceError("forbidden", "Only the poster can dispute", 403, {})

        # Step 12: Validate reason
        reason = payload.get("reason")
        if not isinstance(reason, str) or len(reason) < 1:
            raise ServiceError(
                "invalid_reason",
                "Dispute reason must be a non-empty string",
                400,
                {},
            )

        if len(reason) > 10000:
            raise ServiceError(
                "invalid_reason",
                "Dispute reason must not exceed 10,000 characters",
                400,
                {},
            )

        # Step 13: connect/timeout/HTTP errors from Court map to a typed 502 —
        # the task must stay 'submitted' rather than surface a raw 500 (GAP-E7).
        try:
            court_dispute = await platform_agent.file_claim(
                task_id=task_id,
                claimant_id=str(task["poster_id"]),
                respondent_id=str(task["worker_id"]),
                claim=reason,
                escrow_id=str(task["escrow_id"]),
            )
        except PlatformHttpError as exc:
            raise ServiceError(
                "court_unavailable",
                "Cannot connect to Court",
                502,
                {},
            ) from exc

        # Step 13: A task must never enter 'disputed' without a Court dispute to
        # bind rebuttals to, otherwise any worker could name an arbitrary dispute.
        dispute_id = court_dispute.get("dispute_id")
        if not isinstance(dispute_id, str) or len(dispute_id) < 1:
            raise ServiceError(
                "court_unavailable",
                "Court did not return a dispute id",
                502,
                {},
            )

        # Update task. Persist the Court-authoritative rebuttal deadline alongside the
        # dispute id so the deadline evaluator can fire the ruling once the window closes
        # (GAP-A1); it may be absent if Court did not return one.
        disputed_at = now_iso()
        dispute_updates: dict[str, Any] = {
            "status": "disputed",
            "disputed_at": disputed_at,
            "dispute_reason": reason,
            "dispute_id": dispute_id,
        }
        rebuttal_deadline = court_dispute.get("rebuttal_deadline")
        if isinstance(rebuttal_deadline, str) and rebuttal_deadline != "":
            dispute_updates["rebuttal_deadline"] = rebuttal_deadline
        manager.store.update_task(
            task_id,
            dispute_updates,
            expected_status=None,
        )

        updated = manager.store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        response = task_to_response(updated)
        response["dispute_id"] = dispute_id
        return response

    async def submit_rebuttal(
        self,
        task_id: str,
        token: str,
        platform_agent: PlatformAgent,
    ) -> dict[str, Any]:
        """
        Submit a worker rebuttal via the platform-signed Court path.

        Error precedence:
        1-6. JWS verification
        7.   invalid_payload — wrong action, task_id mismatch, missing fields
        9a.  forbidden — signer != worker_id in payload
        10.  task_not_found
        11.  invalid_status — not DISPUTED
        9b.  forbidden — signer != task's worker
        12a. invalid_status — task has no recorded dispute
        12b. invalid_payload — dispute_id does not match this task's dispute
        13.  invalid_rebuttal — empty or too long
        """
        manager = self._manager
        payload = await manager.token_validator.validate_jws_token(token, "submit_rebuttal")
        signer_id: str = payload["_signer_id"]

        for field in ("task_id", "dispute_id", "worker_id", "rebuttal"):
            if field not in payload:
                raise ServiceError(
                    "invalid_payload",
                    f"Missing required field: {field}",
                    400,
                    {},
                )

        if payload["task_id"] != task_id:
            raise ServiceError(
                "invalid_payload",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        if signer_id != payload["worker_id"]:
            raise ServiceError("forbidden", "Signer does not match worker_id", 403, {})

        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        if task["status"] != "disputed":
            raise ServiceError(
                "invalid_status",
                f"Cannot rebut task in '{task['status']}' status, must be 'disputed'",
                409,
                {},
            )

        if signer_id != task["worker_id"]:
            raise ServiceError("forbidden", "Only the worker can submit a rebuttal", 403, {})

        # Steps 12a-12b: The rebuttal must target the dispute the Court opened for
        # this task. Without this, a worker could inject a rebuttal into any dispute.
        stored_dispute_id = task.get("dispute_id")
        if not stored_dispute_id:
            raise ServiceError("invalid_status", "Task has no recorded dispute", 409, {})

        dispute_id = payload["dispute_id"]
        if not isinstance(dispute_id, str) or len(dispute_id) < 1:
            raise ServiceError("invalid_payload", "dispute_id must be non-empty", 400, {})

        if dispute_id != stored_dispute_id:
            raise ServiceError(
                "invalid_payload",
                "dispute_id does not match this task's dispute",
                400,
                {},
            )

        rebuttal = payload["rebuttal"]
        if not isinstance(rebuttal, str) or len(rebuttal) < 1:
            raise ServiceError(
                "invalid_rebuttal",
                "Rebuttal must be a non-empty string",
                400,
                {},
            )

        if len(rebuttal) > 10000:
            raise ServiceError(
                "invalid_rebuttal",
                "Rebuttal must not exceed 10,000 characters",
                400,
                {},
            )

        # Connect/timeout/HTTP errors from Court map to a typed 502 — the dispute
        # must stay exactly as it was rather than surface a raw 500 (GAP-E7).
        try:
            response: dict[str, Any] = await platform_agent.submit_rebuttal(
                str(stored_dispute_id), rebuttal
            )
        except PlatformHttpError as exc:
            raise ServiceError(
                "court_unavailable",
                "Cannot connect to Court",
                502,
                {},
            ) from exc
        # Record that a rebuttal now exists so the deadline evaluator can fire the ruling
        # immediately, without waiting for the rebuttal window to close (GAP-A1).
        manager.store.update_task(
            task_id,
            {"rebuttal_submitted_at": now_iso()},
            expected_status=None,
        )
        return response

    async def record_ruling(self, task_id: str, token: str) -> dict[str, Any]:
        """
        Record a Court ruling. Platform-signed operation.

        Error precedence:
        1-6. JWS verification
        7.   invalid_payload — wrong action, task_id mismatch, missing fields
        9a.  forbidden — signer is not the platform agent
        10.  task_not_found
        11.  invalid_status — not DISPUTED
        12.  invalid_worker_pct
        """
        manager = self._manager
        # Steps 4-7a: Verify JWS locally (platform op), validate action.
        # T-036: the undocumented 'submit_ruling' alias is removed — record_ruling
        # is the only accepted action.
        payload = await manager.token_validator.validate_platform_jws_token(
            token,
            "record_ruling",
        )
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: task_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "invalid_payload",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 7d: Validate ruling_id present and non-empty
        if "ruling_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: ruling_id", 400, {})

        ruling_id = payload["ruling_id"]
        if not isinstance(ruling_id, str) or len(ruling_id) < 1:
            raise ServiceError(
                "invalid_payload",
                "ruling_id must be a non-empty string",
                400,
                {},
            )

        # Step 7e: Validate ruling_summary present and non-empty
        if "ruling_summary" not in payload:
            raise ServiceError(
                "invalid_payload",
                "Missing required field: ruling_summary",
                400,
                {},
            )

        ruling_summary = payload["ruling_summary"]
        if not isinstance(ruling_summary, str) or len(ruling_summary) < 1:
            raise ServiceError(
                "invalid_payload",
                "ruling_summary must be a non-empty string",
                400,
                {},
            )

        # Step 7f: Validate worker_pct present
        if "worker_pct" not in payload:
            raise ServiceError(
                "invalid_payload",
                "Missing required field: worker_pct",
                400,
                {},
            )

        worker_pct = payload["worker_pct"]

        # Step 9a: Signer must be the platform agent
        if signer_id != manager.platform_agent_id:
            raise ServiceError(
                "forbidden",
                "Only the platform agent can record rulings",
                403,
                {},
            )

        # Step 10: Load task
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        # Idempotent retry (T-040): re-recording the identical ruling_id on an
        # already-ruled task converges to the stored outcome without settling escrow
        # a second time, so a Court retry after a partial failure cannot double-pay.
        if task["status"] == "ruled" and str(task.get("ruling_id")) == ruling_id:
            return task_to_response(task)

        # Step 11: Check status
        if task["status"] != "disputed":
            raise ServiceError(
                "invalid_status",
                f"Cannot record ruling for task in '{task['status']}' status, must be 'disputed'",
                409,
                {},
            )

        # Step 12: Validate worker_pct value (after status check)
        if not is_valid_worker_pct(worker_pct):
            raise ServiceError(
                "invalid_worker_pct",
                "worker_pct must be an integer between 0 and 100",
                400,
                {},
            )

        worker_pct_int = int(worker_pct)

        # Escrow distribution based on ruling:
        # - 0% => full refund to poster
        # - 100% => full payout to worker
        # - otherwise => split between both
        if worker_pct_int == 0:
            await manager.escrow_coordinator.release_escrow(task["escrow_id"], task["poster_id"])
        elif worker_pct_int == 100:
            await manager.escrow_coordinator.release_escrow(task["escrow_id"], task["worker_id"])
        else:
            await manager.escrow_coordinator.split_escrow(
                escrow_id=task["escrow_id"],
                worker_id=task["worker_id"],
                poster_id=task["poster_id"],
                worker_pct=worker_pct_int,
            )

        # Update task
        ruled_at = now_iso()
        manager.store.update_task(
            task_id,
            {
                "status": "ruled",
                "ruled_at": ruled_at,
                "ruling_id": ruling_id,
                "worker_pct": worker_pct_int,
                "ruling_summary": ruling_summary,
            },
            expected_status=None,
        )

        updated = manager.store.get_task(task_id)
        if updated is None:
            msg = f"Task {task_id} not found after update"
            raise RuntimeError(msg)
        return task_to_response(updated)

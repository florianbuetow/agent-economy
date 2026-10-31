"""Dispute, rebuttal, and Court ruling recording (WP-11, B16 god-class decomposition).

Extracted from task_manager.py. See task_creation.py's module docstring for
why coordinators read dependencies through ``self._manager`` rather than a
constructor-time snapshot.
"""

from __future__ import annotations

from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError
from service_commons.telemetry import annotate

from task_board_service.services.task_db_client import PlatformHttpError
from task_board_service.services.task_helpers import is_valid_worker_pct
from task_board_service.services.task_operations import (
    accepted_task_arguments,
    complete_bank_operation,
    complete_court_operation,
    operation_response,
    public_operation_identity,
)

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
        """Claim a poster dispute before creating or recovering its Court effect."""
        annotate(task_id=task_id, action="dispute_task")
        manager = self._manager
        payload = await manager.token_validator.validate_jws_token(
            token,
            "dispute_task",
            expected_resource=f"POST /tasks/{task_id}/dispute",
        )
        signer_id: str = payload["_signer_id"]
        for field in ("task_id", "poster_id"):
            if field not in payload:
                raise ServiceError("invalid_payload", f"Missing required field: {field}", 400, {})
        operation_id, expected_version = public_operation_identity(payload)
        if payload["task_id"] != task_id:
            raise ServiceError("invalid_payload", "task_id does not match URL path", 400, {})
        if signer_id != payload["poster_id"]:
            raise ServiceError("forbidden", "Signer does not match poster_id", 403, {})
        reason = payload.get("reason")
        if not isinstance(reason, str) or not 1 <= len(reason) <= 10000:
            raise ServiceError(
                "invalid_reason", "Dispute reason must be 1 to 10,000 characters", 400, {}
            )
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})
        arguments = accepted_task_arguments(task, expected_version, "submitted")
        arguments["reason"] = reason
        envelope = manager.store.claim_task_operation(
            {
                "protocol_version": 1,
                "operation_id": operation_id,
                "task_id": task_id,
                "command": "dispute_task",
                "expected_status": "submitted",
                "expected_lifecycle_version": expected_version,
                "actor_kind": "agent",
                "actor_id": signer_id,
                "arguments": arguments,
            }
        )
        finalized = await complete_court_operation(manager.store, platform_agent, envelope)
        return operation_response(manager.store, finalized)

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
        annotate(task_id=task_id, action="submit_rebuttal")
        manager = self._manager
        payload = await manager.token_validator.validate_jws_token(
            token,
            "submit_rebuttal",
            expected_resource=f"POST /tasks/{task_id}/rebuttal",
        )
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

        # Transport or malformed acknowledgment errors map to a typed 502.
        # Court owns the atomic rebuttal and its Board timestamp projection.
        try:
            response: dict[str, Any] = await platform_agent.submit_rebuttal(
                str(stored_dispute_id), rebuttal
            )
        except (PlatformHttpError, JSONDecodeError) as exc:
            raise ServiceError(
                "court_unavailable",
                "Cannot connect to Court",
                502,
                {},
            ) from exc
        return response

    async def record_ruling(self, task_id: str, token: str) -> dict[str, Any]:
        """Claim the exact platform ruling before delivering its frozen Bank intent."""
        annotate(task_id=task_id, action="record_ruling")
        manager = self._manager
        payload = await manager.token_validator.validate_platform_jws_token(
            token,
            "record_ruling",
            expected_resource=f"POST /tasks/{task_id}/ruling",
        )
        signer_id: str = payload["_signer_id"]
        for field in ("task_id", "ruling_id", "ruling_summary", "worker_pct"):
            if field not in payload:
                raise ServiceError("invalid_payload", f"Missing required field: {field}", 400, {})
        operation_id, expected_version = public_operation_identity(payload)
        if payload["task_id"] != task_id:
            raise ServiceError("invalid_payload", "task_id does not match URL path", 400, {})
        ruling_id = payload["ruling_id"]
        summary = payload["ruling_summary"]
        if not isinstance(ruling_id, str) or not ruling_id:
            raise ServiceError("invalid_payload", "ruling_id must be a nonempty string", 400, {})
        if not isinstance(summary, str) or not summary:
            raise ServiceError(
                "invalid_payload", "ruling_summary must be a nonempty string", 400, {}
            )
        if signer_id != manager.platform_agent_id:
            raise ServiceError("forbidden", "Only the platform agent can record rulings", 403, {})
        worker_pct = payload["worker_pct"]
        if not is_valid_worker_pct(worker_pct):
            raise ServiceError(
                "invalid_worker_pct", "worker_pct must be an integer between 0 and 100", 400, {}
            )
        task = manager.store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})
        arguments = accepted_task_arguments(task, expected_version, "disputed")
        arguments.update(
            {
                "ruling_id": ruling_id,
                "worker_pct": worker_pct,
                "summary": summary,
                "source_reference": f"task:{task_id}:ruling:{ruling_id}",
            }
        )
        envelope = manager.store.claim_task_operation(
            {
                "protocol_version": 1,
                "operation_id": operation_id,
                "task_id": task_id,
                "command": "record_ruling",
                "expected_status": "disputed",
                "expected_lifecycle_version": expected_version,
                "actor_kind": "platform",
                "actor_id": signer_id,
                "arguments": arguments,
            }
        )
        finalized = await complete_bank_operation(
            manager.store, manager.escrow_coordinator, envelope
        )
        return operation_response(manager.store, finalized)

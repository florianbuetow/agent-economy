"""Resume native Task Board commands using their persisted effect identity."""

from __future__ import annotations

from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from task_board_service.services.task_db_client import PlatformHttpError
from task_board_service.services.task_helpers import task_to_response
from task_board_service.task_operation_contract import (
    MAX_INTEGER,
    invalid_gateway_response,
    validate_operation_id,
)

if TYPE_CHECKING:
    from service_auth.platform import PlatformAgent

    from task_board_service.services.escrow_coordinator import EscrowCoordinator
    from task_board_service.services.protocol import TaskStorageInterface


_IMMUTABLE_FIELDS = (
    "protocol_version",
    "operation_id",
    "task_id",
    "command",
    "request_sha256",
    "source_status",
    "source_lifecycle_version",
    "target_status",
    "target_lifecycle_version",
    "effect_kind",
    "effect_operation_id",
    "effect_intent",
)


def public_operation_identity(payload: dict[str, Any]) -> tuple[str, int]:
    """Read a caller-owned command identity without allocating a retry identity."""
    for field in ("operation_id", "expected_lifecycle_version"):
        if field not in payload:
            raise ServiceError("invalid_payload", f"Missing required field: {field}", 400, {})
    operation_id = validate_operation_id(payload["operation_id"])
    version = payload["expected_lifecycle_version"]
    if type(version) is not int or not 0 <= version <= MAX_INTEGER:
        raise ServiceError(
            "invalid_payload",
            "expected_lifecycle_version must be a nonnegative integer",
            400,
            {},
        )
    return operation_id, version


def operation_response(
    store: TaskStorageInterface,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Keep the immutable command outcome separate from the current task view."""
    task = store.get_task(envelope["task_id"])
    if task is None:
        raise invalid_gateway_response()
    response = task_to_response(task)
    response["operation"] = envelope
    return response


def accepted_task_arguments(
    task: dict[str, Any],
    expected_version: int,
    expected_status: str,
) -> dict[str, Any]:
    """Read frozen accepted terms, or classify a valid but unbuildable request."""
    worker = task.get("worker_id")
    reward = task.get("accepted_reward")
    if worker is None and reward is None and task["status"] in {"open", "cancelled", "expired"}:
        _reject_unbuildable_command(task, expected_version, expected_status)
    poster = task.get("poster_id")
    escrow = task.get("escrow_id")
    if any(not isinstance(value, str) or not value for value in (worker, poster, escrow)) or (
        type(reward) is not int or not 1 <= reward <= MAX_INTEGER
    ):
        raise invalid_gateway_response()
    return {"poster_id": poster, "worker_id": worker, "escrow_id": escrow, "reward": reward}


def _reject_unbuildable_command(
    task: dict[str, Any],
    expected_version: int,
    expected_status: str,
) -> None:
    pending = task["pending_transition"]
    if pending is not None:
        raise ServiceError(
            "transition_pending",
            "Another task transition already owns this task",
            409,
            {"winning_operation_id": pending["operation_id"]},
        )
    legacy = task["legacy_reconciliations"]
    if legacy:
        raise ServiceError(
            "legacy_reconciliation_pending",
            "Task has an unresolved legacy effect observation",
            409,
            {"observation_id": legacy[0]["observation_id"]},
        )
    if expected_version != task["lifecycle_version"]:
        raise ServiceError(
            "task_version_conflict",
            "Task lifecycle version differs from the command precondition",
            409,
            {
                "expected_lifecycle_version": expected_version,
                "actual_lifecycle_version": task["lifecycle_version"],
                "current_status": task["status"],
            },
        )
    raise ServiceError(
        "invalid_status",
        "Task status differs from the command precondition",
        409,
        {"expected_status": expected_status, "current_status": task["status"]},
    )


def _finalize_same_operation(
    store: TaskStorageInterface,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    finalized = store.finalize_task_operation(envelope["operation_id"])
    if any(finalized[field] != envelope[field] for field in _IMMUTABLE_FIELDS):
        raise invalid_gateway_response()
    return finalized


async def complete_bank_operation(
    store: TaskStorageInterface,
    escrow_coordinator: EscrowCoordinator,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Deliver stored intent once per invocation and finalize only from Gateway evidence."""
    if envelope["operation_state"] == "completed":
        return envelope
    pending = _finalize_same_operation(store, envelope)
    if pending["operation_state"] == "completed":
        return pending
    financial = pending["effect_intent"]["financial_request"]
    try:
        if pending["effect_kind"] == "bank_release":
            await escrow_coordinator.release_escrow(
                financial["escrow_id"],
                financial["recipient_account_id"],
                pending["effect_operation_id"],
                financial["source_reference"],
            )
        elif pending["effect_kind"] == "bank_split" and "worker_amount" in financial:
            await escrow_coordinator.settle_escrow(
                financial["escrow_id"],
                financial["worker_account_id"],
                financial["poster_account_id"],
                financial["worker_amount"],
                pending["effect_operation_id"],
                financial["source_reference"],
            )
        elif pending["effect_kind"] == "bank_split":
            await escrow_coordinator.split_escrow(
                financial["escrow_id"],
                financial["worker_account_id"],
                financial["poster_account_id"],
                financial["worker_pct"],
                pending["effect_operation_id"],
                financial["source_reference"],
            )
        else:
            raise invalid_gateway_response()
    except ServiceError:
        # A failed response does not establish that the Bank transaction failed.
        finalized = _finalize_same_operation(store, envelope)
        if finalized["operation_state"] == "completed":
            return finalized
        raise
    return _finalize_same_operation(store, envelope)


async def complete_court_operation(
    store: TaskStorageInterface,
    platform_agent: PlatformAgent,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Find or deliver the claimed Court effect using Gateway-owned claim evidence."""
    if envelope["operation_state"] == "completed":
        return envelope
    pending = _finalize_same_operation(store, envelope)
    if pending["operation_state"] == "completed":
        return pending
    if pending["effect_kind"] != "court_claim":
        raise invalid_gateway_response()
    intent = pending["effect_intent"]
    claim = intent["court_claim"]
    try:
        await platform_agent.file_claim(
            task_id=claim["task_id"],
            claimant_id=claim["claimant_id"],
            respondent_id=claim["respondent_id"],
            claim=claim["reason"],
            escrow_id=intent["expected_escrow"]["escrow_id"],
        )
    except (PlatformHttpError, ServiceError, JSONDecodeError) as exc:
        finalized = _finalize_same_operation(store, envelope)
        if finalized["operation_state"] == "completed":
            return finalized
        if isinstance(exc, ServiceError):
            raise
        raise ServiceError("court_unavailable", "Court claim delivery failed", 502, {}) from exc
    return _finalize_same_operation(store, envelope)

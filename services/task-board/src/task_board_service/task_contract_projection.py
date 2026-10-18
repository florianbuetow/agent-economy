"""Strict native task-contract and recovery fields received from Gateway reads."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from task_board_service.task_operation_contract import (
    COMMANDS,
    Integer,
    OperationId,
    StrictModel,
    Text,
    invalid_gateway_response,
)


class PendingTransition(StrictModel):
    """An active native command; source business state remains unchanged."""

    operation_id: OperationId
    command: Text
    target_status: Text
    target_lifecycle_version: Integer
    operation_state: Literal["claimed", "blocked"]
    effect_state: Literal["pending", "conflict"]


class GatewayBid(StrictModel):
    """Authoritative bid facts cannot be coerced into valid economic terms."""

    bid_id: Text
    task_id: Text
    bidder_id: Text
    proposal: str
    amount: Integer
    submitted_at: Text


class LegacyReconciliation(StrictModel):
    """An observed historical effect, never a fabricated native command."""

    observation_id: Annotated[str, Field(pattern=r"^obs-.+$")]
    observation_kind: Literal[
        "worker_settlement_succeeded", "worker_settlement_pending", "court_claim_succeeded"
    ]
    effect_state: Literal["succeeded", "pending"]
    target_outcome_hint: Literal["approved", "disputed"]


class TaskContractProjection(StrictModel):
    """Required v3 sidecar fields and separately labeled recovery state."""

    lifecycle_version: Integer
    timing_contract_version: Annotated[int, Field(ge=1, le=2)]
    acceptance_window_seconds: Annotated[int, Field(gt=0)] | None
    bidding_cutoff_us: Integer
    acceptance_cutoff_us: Integer
    execution_cutoff_us: Integer | None
    review_cutoff_us: Integer | None
    accepted_reward: Annotated[int, Field(gt=0)] | None
    accepted_bid_amount: Integer | None
    pending_transition: PendingTransition | None
    legacy_reconciliations: list[LegacyReconciliation]


def validate_task_projection(data: dict[str, Any]) -> None:
    """Validate required fields without discarding ordinary task columns."""
    try:
        fields = {key: data[key] for key in TaskContractProjection.model_fields if key in data}
        contract = TaskContractProjection.model_validate(fields)
        window = contract.acceptance_window_seconds
        if contract.timing_contract_version == 1:
            valid_timing = (
                window is None and contract.acceptance_cutoff_us == contract.bidding_cutoff_us
            )
        else:
            valid_timing = (
                window is not None
                and contract.acceptance_cutoff_us == contract.bidding_cutoff_us + window * 1_000_000
            )
        if not valid_timing or (
            (contract.accepted_reward is None) != (contract.accepted_bid_amount is None)
        ):
            raise ValueError("Invalid frozen task terms")
        pending = contract.pending_transition
        if pending is not None and contract.legacy_reconciliations:
            raise ValueError("Native and legacy recovery cannot own the same task")
        if pending is not None:
            source, target, _actor, _arguments = COMMANDS[pending.command]
            expected_effect = "pending" if pending.operation_state == "claimed" else "conflict"
            if (
                source != data["status"]
                or pending.target_status != target
                or pending.target_lifecycle_version != contract.lifecycle_version + 1
                or pending.effect_state != expected_effect
                or pending.command in {"submit_bid", "accept_bid", "submit_deliverable"}
            ):
                raise ValueError("Invalid pending task transition")
        for observation in contract.legacy_reconciliations:
            court = observation.observation_kind == "court_claim_succeeded"
            state = (
                "pending"
                if observation.observation_kind == "worker_settlement_pending"
                else "succeeded"
            )
            if observation.effect_state != state or observation.target_outcome_hint != (
                "disputed" if court else "approved"
            ):
                raise ValueError("Invalid legacy task observation")
    except (ValidationError, ValueError, KeyError, TypeError) as exc:
        raise invalid_gateway_response() from exc

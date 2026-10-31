"""Self-contained MP-08 recovery boundary fixtures.

These fakes model only the protected Gateway and Bank boundaries consumed by
the Task Board recovery worker.  They deliberately do not import helpers from
other test modules.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from service_commons.exceptions import ServiceError

from task_board_service.task_operation_contract import canonical_hash

EFFECT_OPERATION_ID = "op-11111111-1111-4111-8111-111111111111"
SECOND_EFFECT_OPERATION_ID = "op-22222222-2222-4222-8222-222222222222"
TASK_ID = "t-mp08-recovery"
ESCROW_ID = "esc-mp08-recovery"
POSTER_ID = "a-mp08-poster"
WORKER_ACCOUNT_ID = "a-mp08-worker"
RECOVERY_WORKER_ID = "recovery-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def release_request() -> dict[str, object]:
    return {
        "request_version": 1,
        "operation_kind": "escrow_release",
        "escrow_id": ESCROW_ID,
        "recipient_account_id": WORKER_ACCOUNT_ID,
        "source_reference": f"task:{TASK_ID}:approved",
    }


def split_request() -> dict[str, object]:
    return {
        "request_version": 1,
        "operation_kind": "escrow_split",
        "escrow_id": ESCROW_ID,
        "worker_account_id": WORKER_ACCOUNT_ID,
        "poster_account_id": POSTER_ID,
        "worker_pct": 60,
        "source_reference": f"task:{TASK_ID}:ruling:clm-mp08",
    }


def expected_escrow() -> dict[str, object]:
    return {
        "escrow_id": ESCROW_ID,
        "task_id": TASK_ID,
        "payer_account_id": POSTER_ID,
        "amount": 100,
    }


def lease_envelope(
    *,
    effect_operation_id: str = EFFECT_OPERATION_ID,
    effect_kind: str = "bank_release",
    source_kind: str = "native_task_operation",
    source_id: str = EFFECT_OPERATION_ID,
    financial_request: dict[str, object] | None = None,
    generation: int = 3,
) -> dict[str, Any]:
    request = financial_request or release_request()
    return {
        "protocol_version": 1,
        "delivery": {
            "effect_operation_id": effect_operation_id,
            "task_id": TASK_ID,
            "source_kind": source_kind,
            "source_id": source_id,
            "effect_kind": effect_kind,
            "effect_intent": {
                "financial_request": copy.deepcopy(request),
                "request_sha256": canonical_hash(request),
                "expected_escrow": expected_escrow(),
            },
            "worker_id": RECOVERY_WORKER_ID,
            "lease_generation": generation,
            "total_attempt_count": 3,
            "cycle_attempt_count": 2,
            "lease_acquired_at_us": 1_000_000,
            "lease_expires_at_us": 46_000_000,
        },
    }


def empty_lease_envelope() -> dict[str, object]:
    return {"protocol_version": 1, "delivery": None}


def diagnostic(
    *,
    effect_operation_id: str = EFFECT_OPERATION_ID,
    state: str = "completed",
    source_kind: str = "native_task_operation",
    source_id: str = EFFECT_OPERATION_ID,
    effect_kind: str = "bank_release",
) -> dict[str, Any]:
    completed = state == "completed"
    leased = state == "leased"
    return {
        "effect_operation_id": effect_operation_id,
        "task_id": TASK_ID,
        "source_kind": source_kind,
        "source_id": source_id,
        "effect_kind": effect_kind,
        "delivery_state": state,
        "enqueued_at_us": 500_000,
        "observed_at_us": 2_000_000,
        "total_attempt_count": 3,
        "cycle_attempt_count": 2,
        "lease_generation": 3,
        "next_attempt_at_us": 2_000_000 if state == "pending" else None,
        "lease_owner_id": RECOVERY_WORKER_ID if leased else None,
        "lease_acquired_at_us": 1_000_000 if leased else None,
        "lease_expires_at_us": 46_000_000 if leased else None,
        "last_failure_code": "central_bank_unavailable",
        "last_failure_at_us": 900_000,
        "operator_retry_count": 0,
        "last_operator_retry_at_us": None,
        "next_evidence_check_at_us": 3_000_000 if state == "dead_letter" else None,
        "completed_at_us": 1_500_000 if completed else None,
        "completion_event_id": 19 if completed else None,
        "receipt_reference": effect_operation_id if completed else None,
    }


def diagnostic_envelope(
    *,
    effect_operation_id: str = EFFECT_OPERATION_ID,
    state: str = "completed",
    source_kind: str = "native_task_operation",
    source_id: str = EFFECT_OPERATION_ID,
    effect_kind: str = "bank_release",
) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "delivery": diagnostic(
            effect_operation_id=effect_operation_id,
            state=state,
            source_kind=source_kind,
            source_id=source_id,
            effect_kind=effect_kind,
        ),
    }


@dataclass
class GatewayResult:
    """Typed status plus strict body for routes with meaningful 200/202 success."""

    status_code: int
    body: dict[str, Any]


@dataclass
class RecoveryStore:
    """Ordered protected-boundary outcomes with complete call evidence."""

    leases: list[dict[str, Any]] = field(default_factory=list)
    finalize_native: list[dict[str, Any] | ServiceError] = field(default_factory=list)
    finalize_legacy: list[GatewayResult | ServiceError] = field(default_factory=list)
    failures: list[dict[str, Any] | ServiceError] = field(default_factory=list)
    lease_calls: list[str] = field(default_factory=list)
    native_calls: list[str] = field(default_factory=list)
    legacy_calls: list[str] = field(default_factory=list)
    failure_calls: list[tuple[str, int, str, str]] = field(default_factory=list)

    def lease_task_effect_delivery(self, worker_id: str) -> dict[str, Any]:
        self.lease_calls.append(worker_id)
        return self.leases.pop(0) if self.leases else empty_lease_envelope()

    def finalize_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]:
        self.native_calls.append(effect_operation_id)
        outcome = self.finalize_native.pop(0)
        if isinstance(outcome, ServiceError):
            raise outcome
        return outcome

    def finalize_legacy_effect_observation(self, observation_id: str) -> GatewayResult:
        self.legacy_calls.append(observation_id)
        outcome = self.finalize_legacy.pop(0)
        if isinstance(outcome, ServiceError):
            raise outcome
        return outcome

    def fail_task_effect_delivery(
        self,
        effect_operation_id: str,
        lease_generation: int,
        worker_id: str,
        error_code: str,
    ) -> dict[str, Any]:
        self.failure_calls.append((effect_operation_id, lease_generation, worker_id, error_code))
        outcome = self.failures.pop(0)
        if isinstance(outcome, ServiceError):
            raise outcome
        return outcome


@dataclass
class RecoveryBank:
    """Bank boundary that records the exact immutable operation request."""

    release_outcomes: list[object] = field(default_factory=list)
    split_outcomes: list[object] = field(default_factory=list)
    release_calls: list[dict[str, object]] = field(default_factory=list)
    split_calls: list[dict[str, object]] = field(default_factory=list)

    async def escrow_release(self, **kwargs: object) -> object:
        self.release_calls.append(kwargs)
        outcome = self.release_outcomes.pop(0) if self.release_outcomes else {}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def escrow_split(self, **kwargs: object) -> object:
        self.split_calls.append(kwargs)
        outcome = self.split_outcomes.pop(0) if self.split_outcomes else {}
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

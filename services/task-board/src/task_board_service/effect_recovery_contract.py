"""Strict Task Board client contract for MP-08 financial-effect recovery."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from service_commons.exceptions import ServiceError

from task_board_service.task_operation_contract import MAX_INTEGER, canonical_hash

GatewayInteger = Annotated[int, Field(strict=True, ge=0, le=MAX_INTEGER)]
PositiveGatewayInteger = Annotated[int, Field(strict=True, gt=0, le=MAX_INTEGER)]
GatewayTime = Annotated[int, Field(strict=True, ge=0, le=253_402_300_799_999_999)]
Text = Annotated[str, Field(strict=True, min_length=1)]
OperationId = Annotated[
    str,
    Field(pattern=r"^op-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]
ObservationId = Annotated[
    str,
    Field(pattern=r"^obs-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]
RecoveryWorkerId = Annotated[
    str,
    Field(
        pattern=(
            r"^recovery-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    ),
]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_OPERATION_ID_ADAPTER = TypeAdapter(OperationId)
_OBSERVATION_ID_ADAPTER = TypeAdapter(ObservationId)
_RECOVERY_WORKER_ID_ADAPTER = TypeAdapter(RecoveryWorkerId)


class StrictModel(BaseModel):
    """Reject coercions and fields outside the versioned wire contract."""

    model_config = ConfigDict(strict=True, extra="forbid")


class ExpectedEscrow(StrictModel):
    """Immutable escrow predicate supplied by Gateway."""

    escrow_id: Text
    task_id: Text
    payer_account_id: Text
    amount: PositiveGatewayInteger


class ReleaseRequest(StrictModel):
    """Canonical version-one escrow-release request."""

    request_version: Literal[1]
    operation_kind: Literal["escrow_release"]
    escrow_id: Text
    recipient_account_id: Text
    source_reference: Text


class SplitRequest(StrictModel):
    """Canonical version-one escrow-split request."""

    request_version: Literal[1]
    operation_kind: Literal["escrow_split"]
    escrow_id: Text
    worker_account_id: Text
    poster_account_id: Text
    worker_pct: Annotated[int, Field(strict=True, gt=0, lt=100)]
    source_reference: Text


class SettlementRequest(StrictModel):
    """Canonical version-one exact-amount escrow split (G01 accepted-bid settlement)."""

    request_version: Literal[1]
    operation_kind: Literal["escrow_split"]
    escrow_id: Text
    worker_account_id: Text
    poster_account_id: Text
    worker_amount: PositiveGatewayInteger
    source_reference: Text


class EffectIntent(StrictModel):
    """Persisted immutable financial request and its authoritative predicates."""

    financial_request: ReleaseRequest | SplitRequest | SettlementRequest
    request_sha256: Sha256
    expected_escrow: ExpectedEscrow

    @model_validator(mode="after")
    def validate_binding(self) -> EffectIntent:
        request = self.financial_request.model_dump()
        if canonical_hash(request) != self.request_sha256:
            raise ValueError("Financial request hash does not match")
        if request["escrow_id"] != self.expected_escrow.escrow_id:
            raise ValueError("Financial request escrow does not match")
        if (
            isinstance(self.financial_request, SettlementRequest)
            and self.financial_request.worker_amount >= self.expected_escrow.amount
        ):
            raise ValueError("Settlement payment must leave a refund inside the escrow")
        return self


class EffectDeliveryLease(StrictModel):
    """One fenced, caller-owned recovery attempt."""

    effect_operation_id: OperationId
    task_id: Text
    source_kind: Literal["native_task_operation", "legacy_settlement_observation"]
    source_id: Text
    effect_kind: Literal["bank_release", "bank_split"]
    effect_intent: EffectIntent
    worker_id: RecoveryWorkerId
    lease_generation: PositiveGatewayInteger
    total_attempt_count: PositiveGatewayInteger
    cycle_attempt_count: PositiveGatewayInteger
    lease_acquired_at_us: GatewayTime
    lease_expires_at_us: GatewayTime

    @model_validator(mode="after")
    def validate_identity(self) -> EffectDeliveryLease:
        if self.task_id != self.effect_intent.expected_escrow.task_id:
            raise ValueError("Lease task does not match escrow predicate")
        if self.effect_kind == "bank_release":
            if not isinstance(self.effect_intent.financial_request, ReleaseRequest):
                raise ValueError("Release lease has non-release intent")
        elif not isinstance(self.effect_intent.financial_request, SplitRequest | SettlementRequest):
            raise ValueError("Split lease has non-split intent")
        if self.source_kind == "native_task_operation":
            if self.source_id != self.effect_operation_id:
                raise ValueError("Native source identity does not match effect identity")
        else:
            if not _is_observation_id(self.source_id):
                raise ValueError("Legacy source identity is invalid")
            if self.effect_kind != "bank_release":
                raise ValueError("Legacy settlement recovery supports releases only")
            request = self.effect_intent.financial_request
            if not isinstance(request, ReleaseRequest):
                raise ValueError("Legacy settlement recovery requires a release intent")
            if request.source_reference != f"task:{self.task_id}:approved":
                raise ValueError("Legacy release source reference is not the approved task")
        if self.lease_expires_at_us <= self.lease_acquired_at_us:
            raise ValueError("Lease expiry must follow acquisition")
        if self.lease_generation != self.total_attempt_count:
            raise ValueError("Lease generation and total attempt count diverged")
        if self.cycle_attempt_count > self.total_attempt_count:
            raise ValueError("Cycle attempt count exceeds total attempt count")
        return self


class LeaseEnvelope(StrictModel):
    """Versioned lease response."""

    protocol_version: Literal[1]
    delivery: EffectDeliveryLease | None


class EffectDeliveryDiagnostic(StrictModel):
    """Safe Gateway scheduling projection for one financial effect."""

    effect_operation_id: OperationId
    task_id: Text
    source_kind: Literal["native_task_operation", "legacy_settlement_observation"]
    source_id: Text
    effect_kind: Literal["bank_release", "bank_split"]
    delivery_state: Literal["pending", "leased", "dead_letter", "completed", "integrity_blocked"]
    enqueued_at_us: GatewayTime
    observed_at_us: GatewayTime
    total_attempt_count: GatewayInteger
    cycle_attempt_count: GatewayInteger
    lease_generation: GatewayInteger
    next_attempt_at_us: GatewayTime | None
    lease_owner_id: RecoveryWorkerId | None
    lease_acquired_at_us: GatewayTime | None
    lease_expires_at_us: GatewayTime | None
    last_failure_code: Text | None
    last_failure_at_us: GatewayTime | None
    operator_retry_count: GatewayInteger
    last_operator_retry_at_us: GatewayTime | None
    next_evidence_check_at_us: GatewayTime | None
    completed_at_us: GatewayTime | None
    completion_event_id: PositiveGatewayInteger | None
    receipt_reference: OperationId | None

    @model_validator(mode="after")
    def validate_state(self) -> EffectDeliveryDiagnostic:
        leased_values = (
            self.lease_owner_id,
            self.lease_acquired_at_us,
            self.lease_expires_at_us,
        )
        if self.delivery_state == "leased":
            if any(value is None for value in leased_values):
                raise ValueError("Leased diagnostic lacks its fence")
        elif any(value is not None for value in leased_values):
            raise ValueError("Non-leased diagnostic exposes an active fence")
        if self.delivery_state == "pending":
            if self.next_attempt_at_us is None or self.next_evidence_check_at_us is not None:
                raise ValueError("Pending diagnostic has invalid scheduling fields")
        elif self.delivery_state == "dead_letter":
            if self.next_attempt_at_us is not None or self.next_evidence_check_at_us is None:
                raise ValueError("Dead-letter diagnostic has invalid scheduling fields")
        elif self.next_attempt_at_us is not None or self.next_evidence_check_at_us is not None:
            raise ValueError("Nonscheduled diagnostic exposes a schedule")
        if self.delivery_state == "completed":
            if (
                self.completed_at_us is None
                or self.completion_event_id is None
                or self.receipt_reference != self.effect_operation_id
            ):
                raise ValueError("Completed diagnostic lacks authoritative receipt evidence")
        elif self.completed_at_us is not None or self.completion_event_id is not None:
            raise ValueError("Incomplete diagnostic exposes completion bookkeeping")
        if (self.last_failure_code is None) != (self.last_failure_at_us is None):
            raise ValueError("Failure diagnostic fields must be paired")
        if (self.operator_retry_count == 0) != (self.last_operator_retry_at_us is None):
            raise ValueError("Operator retry diagnostic fields are inconsistent")
        return self


class DiagnosticEnvelope(StrictModel):
    """Versioned single-delivery diagnostic response."""

    protocol_version: Literal[1]
    delivery: EffectDeliveryDiagnostic


def _is_observation_id(value: str) -> bool:
    try:
        _OBSERVATION_ID_ADAPTER.validate_python(value, strict=True)
    except ValidationError:
        return False
    return True


def invalid_gateway_response() -> ServiceError:
    """Return the bounded response used for all malformed Gateway envelopes."""
    return ServiceError("invalid_gateway_response", "Gateway returned an invalid response", 502, {})


def parse_lease_envelope(value: object) -> LeaseEnvelope:
    """Validate one exact lease envelope."""
    try:
        return LeaseEnvelope.model_validate(value)
    except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise invalid_gateway_response() from exc


def parse_diagnostic_envelope(
    value: object,
    *,
    lease: EffectDeliveryLease,
    required_state: Literal["leased", "completed"] | None,
) -> EffectDeliveryDiagnostic:
    """Validate a diagnostic and bind it to the leased immutable identity."""
    try:
        diagnostic = DiagnosticEnvelope.model_validate(value).delivery
        if (
            diagnostic.effect_operation_id != lease.effect_operation_id
            or diagnostic.task_id != lease.task_id
            or diagnostic.source_kind != lease.source_kind
            or diagnostic.source_id != lease.source_id
            or diagnostic.effect_kind != lease.effect_kind
        ):
            raise ValueError("Diagnostic identity does not match lease")
        if required_state is not None and diagnostic.delivery_state != required_state:
            raise ValueError("Diagnostic state is not valid for this response")
        if diagnostic.delivery_state == "leased" and (
            diagnostic.lease_owner_id != lease.worker_id
            or diagnostic.lease_generation != lease.lease_generation
            or diagnostic.total_attempt_count != lease.total_attempt_count
            or diagnostic.cycle_attempt_count != lease.cycle_attempt_count
            or diagnostic.lease_acquired_at_us != lease.lease_acquired_at_us
            or diagnostic.lease_expires_at_us != lease.lease_expires_at_us
            or diagnostic.enqueued_at_us > lease.lease_acquired_at_us
            or diagnostic.observed_at_us < lease.lease_acquired_at_us
            or diagnostic.observed_at_us >= lease.lease_expires_at_us
            or diagnostic.next_attempt_at_us is not None
            or diagnostic.next_evidence_check_at_us is not None
            or diagnostic.receipt_reference is not None
        ):
            raise ValueError("Leased diagnostic does not match the exact active fence")
        return diagnostic
    except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise invalid_gateway_response() from exc


def parse_failure_envelope(value: object, *, effect_operation_id: str) -> None:
    """Validate a failure-report result without trusting mutable scheduling state."""
    try:
        diagnostic = DiagnosticEnvelope.model_validate(value).delivery
        if diagnostic.effect_operation_id != effect_operation_id:
            raise ValueError("Failure response identity does not match")
        if diagnostic.delivery_state not in {"pending", "dead_letter", "completed"}:
            raise ValueError("Failure response has an invalid HTTP-200 state")
    except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise invalid_gateway_response() from exc


def validate_recovery_worker_id(value: object) -> str:
    """Validate a canonical process-fence identifier."""
    try:
        return _RECOVERY_WORKER_ID_ADAPTER.validate_python(value, strict=True)
    except ValidationError as exc:
        raise ValueError("worker_id must be a canonical recovery UUID") from exc


def failure_identity(value: object) -> tuple[str, int] | None:
    """Extract only enough trusted fence data to report a malformed lease."""
    if not isinstance(value, dict):
        return None
    delivery = value.get("delivery")
    if not isinstance(delivery, dict):
        return None
    operation_id = delivery.get("effect_operation_id")
    if delivery.get("source_kind") == "native_task_operation":
        operation_id = delivery.get("source_id")
    generation = delivery.get("lease_generation")
    if type(generation) is not int:
        generation = delivery.get("total_attempt_count")
    if type(generation) is not int or generation <= 0:
        return None
    try:
        validated_operation_id = _OPERATION_ID_ADAPTER.validate_python(operation_id, strict=True)
    except ValidationError:
        return None
    return validated_operation_id, generation


__all__ = [
    "EffectDeliveryDiagnostic",
    "EffectDeliveryLease",
    "ReleaseRequest",
    "SplitRequest",
    "failure_identity",
    "invalid_gateway_response",
    "parse_diagnostic_envelope",
    "parse_failure_envelope",
    "parse_lease_envelope",
    "validate_recovery_worker_id",
]

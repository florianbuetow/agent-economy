"""Pure durable-queue transition algebra shared by recovery adapters."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class DeliveryQueueState:
    """Persistence-independent queue fields for one durable delivery."""

    item_id: str
    delivery_state: str
    total_attempt_count: int
    cycle_attempt_count: int
    lease_generation: int
    next_attempt_at_us: int | None
    lease_owner_id: str | None
    lease_acquired_at_us: int | None
    lease_expires_at_us: int | None
    last_failure_code: str | None
    last_failure_at_us: int | None
    operator_retry_count: int
    last_operator_retry_at_us: int | None
    last_operator_retry_caller_id: str | None
    next_evidence_check_at_us: int | None


@dataclass(frozen=True, slots=True)
class DeliveryQueuePolicy:
    """Explicit arithmetic and scheduling limits for queue transitions."""

    max_attempts_per_delivery: int
    counter_max: int
    timestamp_max_us: int
    initial_backoff_us: int
    max_backoff_us: int
    lease_duration_us: int
    evidence_interval_us: int


@dataclass(frozen=True, slots=True)
class ApplyTransition:
    """A pure transition that the adapter may persist atomically."""

    state: DeliveryQueueState


@dataclass(frozen=True, slots=True)
class EvidenceRequired:
    """The adapter must inspect evidence before applying the absent path."""

    failure_at_us: int
    failure_code: str
    reason: str


@dataclass(frozen=True, slots=True)
class RejectTransition:
    """A pure rejection; the input state remains unchanged."""

    reason: str


TransitionPlan = ApplyTransition | EvidenceRequired | RejectTransition


def effective_due_us(state: DeliveryQueueState) -> int | None:
    """Return the state-specific schedule used by combined queue ordering."""
    if state.delivery_state == "pending":
        return state.next_attempt_at_us
    if state.delivery_state == "leased":
        return state.lease_expires_at_us
    return None


def plan_lease(
    state: DeliveryQueueState,
    policy: DeliveryQueuePolicy,
    now_us: int,
    worker_id: str,
) -> TransitionPlan:
    """Lease one exact due pending item or request exhaustion evidence."""
    if (
        state.delivery_state != "pending"
        or state.next_attempt_at_us is None
        or state.next_attempt_at_us > now_us
    ):
        return RejectTransition("not_due")
    if not _timestamp_valid(now_us, policy):
        return RejectTransition("clock_exhausted")
    exhausted = _lease_exhaustion(state, policy, now_us)
    if exhausted is not None:
        return exhausted
    expires_at = _checked_add(now_us, policy.lease_duration_us, policy)
    if expires_at is None:
        return RejectTransition("clock_exhausted")
    return ApplyTransition(
        replace(
            state,
            delivery_state="leased",
            total_attempt_count=state.total_attempt_count + 1,
            cycle_attempt_count=state.cycle_attempt_count + 1,
            lease_generation=state.lease_generation + 1,
            next_attempt_at_us=None,
            lease_owner_id=worker_id,
            lease_acquired_at_us=now_us,
            lease_expires_at_us=expires_at,
            next_evidence_check_at_us=None,
        )
    )


def plan_expiry(
    state: DeliveryQueueState,
    policy: DeliveryQueuePolicy,
    now_us: int,
) -> TransitionPlan:
    """Normalize one due expired lease, anchored to its stored expiry."""
    if state.delivery_state != "leased" or state.lease_expires_at_us is None:
        return RejectTransition("stale")
    if state.lease_expires_at_us > now_us:
        return RejectTransition("not_due")
    expiry = state.lease_expires_at_us
    if state.cycle_attempt_count >= policy.max_attempts_per_delivery:
        return EvidenceRequired(
            expiry,
            "lease_expired",
            "attempt_budget_exhausted",
        )
    next_attempt = _checked_add(
        expiry,
        _backoff_us(state.cycle_attempt_count, policy),
        policy,
    )
    if next_attempt is None:
        return RejectTransition("clock_exhausted")
    return ApplyTransition(
        replace(
            state,
            delivery_state="pending",
            next_attempt_at_us=next_attempt,
            lease_owner_id=None,
            lease_acquired_at_us=None,
            lease_expires_at_us=None,
            last_failure_code="lease_expired",
            last_failure_at_us=expiry,
        )
    )


def plan_failure(
    state: DeliveryQueueState,
    policy: DeliveryQueuePolicy,
    failed_at_us: int,
    worker_id: str,
    lease_generation: int,
    failure_code: str,
) -> TransitionPlan:
    """Plan one exact unexpired worker failure with fence-first precedence."""
    if (
        state.delivery_state != "leased"
        or state.lease_owner_id != worker_id
        or state.lease_generation != lease_generation
        or state.lease_expires_at_us is None
        or failed_at_us >= state.lease_expires_at_us
    ):
        return RejectTransition("stale")
    if not _timestamp_valid(failed_at_us, policy):
        return RejectTransition("clock_exhausted")
    if state.cycle_attempt_count >= policy.max_attempts_per_delivery:
        return EvidenceRequired(
            failed_at_us,
            failure_code,
            "attempt_budget_exhausted",
        )
    next_attempt = _checked_add(
        failed_at_us,
        _backoff_us(state.cycle_attempt_count, policy),
        policy,
    )
    if next_attempt is None:
        return RejectTransition("clock_exhausted")
    return ApplyTransition(
        replace(
            state,
            delivery_state="pending",
            next_attempt_at_us=next_attempt,
            lease_owner_id=None,
            lease_acquired_at_us=None,
            lease_expires_at_us=None,
            last_failure_code=failure_code,
            last_failure_at_us=failed_at_us,
        )
    )


def plan_operator_retry(
    state: DeliveryQueueState,
    policy: DeliveryQueuePolicy,
    retried_at_us: int,
    caller_id: str,
) -> TransitionPlan:
    """Plan a dead-letter retry without resetting lifetime identity or history."""
    if state.delivery_state != "dead_letter":
        return RejectTransition("not_retryable")
    if any(
        value >= policy.counter_max
        for value in (
            state.total_attempt_count,
            state.lease_generation,
            state.operator_retry_count,
        )
    ):
        return RejectTransition("counter_exhausted")
    if not _timestamp_valid(retried_at_us, policy):
        return RejectTransition("clock_exhausted")
    return ApplyTransition(
        replace(
            state,
            delivery_state="pending",
            cycle_attempt_count=0,
            next_attempt_at_us=retried_at_us,
            next_evidence_check_at_us=None,
            operator_retry_count=state.operator_retry_count + 1,
            last_operator_retry_at_us=retried_at_us,
            last_operator_retry_caller_id=caller_id,
        )
    )


def plan_dead_letter_evidence_absent(
    state: DeliveryQueueState,
    policy: DeliveryQueuePolicy,
    observed_at_us: int,
) -> TransitionPlan:
    """Reschedule one dead-letter evidence scan without touching counters."""
    if state.delivery_state != "dead_letter":
        return RejectTransition("not_retryable")
    next_check = _checked_add(observed_at_us, policy.evidence_interval_us, policy)
    if next_check is None:
        return RejectTransition("clock_exhausted")
    return ApplyTransition(replace(state, next_evidence_check_at_us=next_check))


def plan_evidence_absent(
    state: DeliveryQueueState,
    policy: DeliveryQueuePolicy,
    required: EvidenceRequired,
) -> ApplyTransition | RejectTransition:
    """Plan dead-letter scheduling only after the adapter proves evidence absent."""
    absent = _dead_letter_absent(
        state,
        policy,
        required.failure_at_us,
        required.failure_code,
    )
    if absent is None:
        return RejectTransition("clock_exhausted")
    return ApplyTransition(absent)


def _dead_letter_absent(
    state: DeliveryQueueState,
    policy: DeliveryQueuePolicy,
    failed_at_us: int,
    failure_code: str,
) -> DeliveryQueueState | None:
    next_check = _checked_add(failed_at_us, policy.evidence_interval_us, policy)
    if next_check is None:
        return None
    return replace(
        state,
        delivery_state="dead_letter",
        next_attempt_at_us=None,
        lease_owner_id=None,
        lease_acquired_at_us=None,
        lease_expires_at_us=None,
        last_failure_code=failure_code,
        last_failure_at_us=failed_at_us,
        next_evidence_check_at_us=next_check,
    )


def _lease_exhaustion(
    state: DeliveryQueueState,
    policy: DeliveryQueuePolicy,
    now_us: int,
) -> EvidenceRequired | RejectTransition | None:
    if state.total_attempt_count >= policy.counter_max:
        reason = "attempt_counter_exhausted"
    elif state.lease_generation >= policy.counter_max:
        reason = "lease_generation_exhausted"
    else:
        return None
    return EvidenceRequired(now_us, reason, reason)


def _backoff_us(attempt: int, policy: DeliveryQueuePolicy) -> int:
    value = policy.initial_backoff_us
    remaining = max(0, attempt - 1)
    while remaining > 0 and value < policy.max_backoff_us:
        value = min(policy.max_backoff_us, value * 2)
        remaining -= 1
    return value


def _checked_add(
    start_us: int,
    delta_us: int,
    policy: DeliveryQueuePolicy,
) -> int | None:
    if not _timestamp_valid(start_us, policy) or delta_us < 0:
        return None
    if start_us > policy.timestamp_max_us - delta_us:
        return None
    return start_us + delta_us


def _timestamp_valid(value: int, policy: DeliveryQueuePolicy) -> bool:
    return type(value) is int and 0 <= value <= policy.timestamp_max_us


__all__ = [
    "ApplyTransition",
    "DeliveryQueuePolicy",
    "DeliveryQueueState",
    "EvidenceRequired",
    "RejectTransition",
    "effective_due_us",
    "plan_dead_letter_evidence_absent",
    "plan_evidence_absent",
    "plan_expiry",
    "plan_failure",
    "plan_lease",
    "plan_operator_retry",
]

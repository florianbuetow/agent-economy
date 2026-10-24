"""Pure queue-transition contract shared by MP-08 and later MP-10 work."""

from __future__ import annotations

import ast
from dataclasses import fields, replace
from pathlib import Path

import pytest

from db_gateway_service.effect_delivery_transitions import (
    ApplyTransition,
    DeliveryQueuePolicy,
    DeliveryQueueState,
    EvidenceRequired,
    RejectTransition,
    effective_due_us,
    plan_dead_letter_evidence_absent,
    plan_evidence_absent,
    plan_expiry,
    plan_failure,
    plan_lease,
    plan_operator_retry,
)

pytestmark = pytest.mark.unit

_MAX = 1_000_000
_WORKER = "recovery-00000000-0000-4000-8000-000000000001"


def _policy() -> DeliveryQueuePolicy:
    return DeliveryQueuePolicy(
        max_attempts_per_delivery=8,
        counter_max=20,
        timestamp_max_us=_MAX,
        initial_backoff_us=2,
        max_backoff_us=60,
        lease_duration_us=45,
        evidence_interval_us=60,
    )


def _pending() -> DeliveryQueueState:
    return DeliveryQueueState(
        item_id="op-00000000-0000-4000-8000-000000000001",
        delivery_state="pending",
        total_attempt_count=3,
        cycle_attempt_count=2,
        lease_generation=4,
        next_attempt_at_us=100,
        lease_owner_id=None,
        lease_acquired_at_us=None,
        lease_expires_at_us=None,
        last_failure_code="request_timeout",
        last_failure_at_us=90,
        operator_retry_count=1,
        last_operator_retry_at_us=80,
        last_operator_retry_caller_id="gateway-task-board",
        next_evidence_check_at_us=None,
    )


def _leased() -> DeliveryQueueState:
    return replace(
        _pending(),
        delivery_state="leased",
        total_attempt_count=4,
        cycle_attempt_count=3,
        lease_generation=5,
        next_attempt_at_us=None,
        lease_owner_id=_WORKER,
        lease_acquired_at_us=100,
        lease_expires_at_us=145,
    )


def test_exact_due_lease_increments_all_three_fence_counters() -> None:
    result = plan_lease(_pending(), _policy(), 100, _WORKER)
    assert isinstance(result, ApplyTransition)
    assert result.state.total_attempt_count == 4
    assert result.state.cycle_attempt_count == 3
    assert result.state.lease_generation == 5
    assert result.state.lease_owner_id == _WORKER
    assert result.state.lease_acquired_at_us == 100
    assert result.state.lease_expires_at_us == 145


def test_future_pending_is_not_due_and_input_is_unchanged() -> None:
    state = replace(_pending(), next_attempt_at_us=101)
    result = plan_lease(state, _policy(), 100, _WORKER)
    assert result == RejectTransition("not_due")
    assert state.next_attempt_at_us == 101


def test_attempt_n_requires_evidence_but_n_minus_one_backs_off() -> None:
    policy = _policy()
    attempt_n = replace(_leased(), cycle_attempt_count=8, total_attempt_count=8)
    required = plan_failure(attempt_n, policy, 120, _WORKER, 5, "request_timeout")
    assert isinstance(required, EvidenceRequired)
    assert required.reason == "attempt_budget_exhausted"
    assert required.failure_code == "request_timeout"
    assert required.failure_at_us == 120
    absent = plan_evidence_absent(attempt_n, policy, required)
    assert isinstance(absent, ApplyTransition)
    assert absent.state.delivery_state == "dead_letter"
    ordinary = plan_failure(
        replace(attempt_n, cycle_attempt_count=7, total_attempt_count=7),
        policy,
        120,
        _WORKER,
        5,
        "request_timeout",
    )
    assert isinstance(ordinary, ApplyTransition)
    assert ordinary.state.next_attempt_at_us == 180


def test_expiry_anchors_failure_and_backoff_to_stored_expiry() -> None:
    result = plan_expiry(_leased(), _policy(), 200)
    assert isinstance(result, ApplyTransition)
    assert result.state.last_failure_code == "lease_expired"
    assert result.state.last_failure_at_us == 145
    assert result.state.next_attempt_at_us == 153


def test_failure_at_exact_expiry_is_stale() -> None:
    result = plan_failure(_leased(), _policy(), 145, _WORKER, 5, "request_timeout")
    assert result == RejectTransition("stale")


def test_wrong_worker_or_generation_rejects_without_changing_input() -> None:
    state = _leased()
    wrong_worker = plan_failure(state, _policy(), 120, "recovery-wrong", 5, "request_timeout")
    wrong_generation = plan_failure(state, _policy(), 120, _WORKER, 6, "request_timeout")
    assert wrong_worker == RejectTransition("stale")
    assert wrong_generation == RejectTransition("stale")
    assert state == _leased()


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("total_attempt_count", "attempt_counter_exhausted"),
        ("lease_generation", "lease_generation_exhausted"),
    ],
)
def test_lease_counter_exhaustion_requires_distinct_evidence_reason(
    field: str,
    reason: str,
) -> None:
    result = plan_lease(replace(_pending(), **{field: 20}), _policy(), 100, _WORKER)
    assert isinstance(result, EvidenceRequired)
    assert result.reason == reason
    absent = plan_evidence_absent(replace(_pending(), **{field: 20}), _policy(), result)
    assert isinstance(absent, ApplyTransition)
    assert absent.state.delivery_state == "dead_letter"


def test_operator_retry_preserves_identity_history_and_lifetime_fences() -> None:
    state = replace(
        _pending(),
        delivery_state="dead_letter",
        cycle_attempt_count=8,
        next_attempt_at_us=None,
        next_evidence_check_at_us=160,
    )
    result = plan_operator_retry(state, _policy(), 170, "gateway-task-board")
    assert isinstance(result, ApplyTransition)
    retried = result.state
    assert retried.item_id == state.item_id
    assert retried.total_attempt_count == state.total_attempt_count
    assert retried.lease_generation == state.lease_generation
    assert retried.last_failure_code == state.last_failure_code
    assert retried.last_failure_at_us == state.last_failure_at_us
    assert retried.cycle_attempt_count == 0
    assert retried.operator_retry_count == state.operator_retry_count + 1


@pytest.mark.parametrize(
    "transition",
    [
        lambda: plan_lease(replace(_pending(), next_attempt_at_us=_MAX), _policy(), _MAX, _WORKER),
        lambda: plan_failure(
            replace(_leased(), lease_expires_at_us=_MAX),
            _policy(),
            _MAX - 1,
            _WORKER,
            5,
            "request_timeout",
        ),
        lambda: plan_operator_retry(
            replace(
                _pending(),
                delivery_state="dead_letter",
                next_attempt_at_us=None,
                next_evidence_check_at_us=100,
                operator_retry_count=20,
            ),
            _policy(),
            200,
            "gateway-task-board",
        ),
    ],
)
def test_counter_and_time_overflow_reject_without_mutation(transition: object) -> None:
    result = transition()  # type: ignore[operator]
    assert isinstance(result, RejectTransition)
    assert result.reason in {"clock_exhausted", "counter_exhausted"}


@pytest.mark.parametrize("field", ["total_attempt_count", "lease_generation"])
def test_counter_ceiling_requires_receipt_before_absent_schedule_overflow(field: str) -> None:
    state = replace(_pending(), **{field: 20}, next_attempt_at_us=_MAX)
    required = plan_lease(state, _policy(), _MAX, _WORKER)
    assert isinstance(required, EvidenceRequired)
    absent = plan_evidence_absent(state, _policy(), required)
    assert absent == RejectTransition("clock_exhausted")


def test_dead_letter_evidence_absence_reschedules_without_counter_changes() -> None:
    state = replace(
        _pending(),
        delivery_state="dead_letter",
        next_attempt_at_us=None,
        next_evidence_check_at_us=100,
    )
    result = plan_dead_letter_evidence_absent(state, _policy(), 120)
    assert isinstance(result, ApplyTransition)
    assert result.state.next_evidence_check_at_us == 180
    for name in ("total_attempt_count", "cycle_attempt_count", "lease_generation"):
        assert getattr(result.state, name) == getattr(state, name)


def test_effective_due_uses_pending_or_lease_schedule_only() -> None:
    assert effective_due_us(_pending()) == 100
    assert effective_due_us(_leased()) == 145
    assert (
        effective_due_us(
            replace(
                _pending(),
                delivery_state="completed",
                next_attempt_at_us=None,
            )
        )
        is None
    )


def test_pure_transition_module_has_no_service_or_persistence_imports() -> None:
    source = Path(__file__).parents[2] / "src/db_gateway_service/effect_delivery_transitions.py"
    tree = ast.parse(source.read_text())
    imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert all(
        not name.startswith(("sqlite3", "service_", "db_gateway_service.config"))
        for name in imports
    )
    assert len(fields(DeliveryQueueState)) == 15

"""MP-08 Task Board financial recovery worker acceptance contract."""

from __future__ import annotations

import copy
import importlib
from typing import Protocol, cast

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.board_mp08_recovery import (
    EFFECT_OPERATION_ID,
    ESCROW_ID,
    POSTER_ID,
    RECOVERY_WORKER_ID,
    TASK_ID,
    WORKER_ACCOUNT_ID,
    GatewayResult,
    RecoveryBank,
    RecoveryStore,
    diagnostic_envelope,
    empty_lease_envelope,
    lease_envelope,
    split_request,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _Recovery(Protocol):
    async def run_cycle(self) -> int: ...


def _worker(store: RecoveryStore, bank: RecoveryBank, *, cycle_limit: int = 10) -> _Recovery:
    try:
        module = importlib.import_module("task_board_service.services.financial_effect_recovery")
    except ModuleNotFoundError as exc:
        if exc.name != "task_board_service.services.financial_effect_recovery":
            raise
        pytest.fail("Missing MP-08 FinancialEffectRecovery capability")
    recovery_type = getattr(module, "FinancialEffectRecovery", None)
    if not callable(recovery_type):
        pytest.fail("Missing callable FinancialEffectRecovery capability")
    return cast(
        "_Recovery",
        recovery_type(
            store=store,
            bank=bank,
            worker_id=RECOVERY_WORKER_ID,
            max_delivery_attempts_per_cycle=cycle_limit,
        ),
    )


async def test_empty_cycle_still_leases_once_for_dead_letter_evidence_progress() -> None:
    store = RecoveryStore(leases=[empty_lease_envelope()])
    bank = RecoveryBank()

    processed = await _worker(store, bank).run_cycle()

    assert processed == 0
    assert store.lease_calls == [RECOVERY_WORKER_ID]
    assert bank.release_calls == []
    assert bank.split_calls == []


async def test_authoritative_receipt_is_checked_before_bank_dispatch() -> None:
    completed = diagnostic_envelope()
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[completed],
    )
    bank = RecoveryBank()

    processed = await _worker(store, bank).run_cycle()

    assert processed == 1
    assert store.native_calls == [EFFECT_OPERATION_ID]
    assert store.failure_calls == []
    assert bank.release_calls == []
    assert bank.split_calls == []


async def test_release_dispatch_uses_only_the_persisted_immutable_intent() -> None:
    pending = diagnostic_envelope(state="leased")
    completed = diagnostic_envelope()
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[pending, completed],
    )
    bank = RecoveryBank(release_outcomes=[{"untrusted_ack": True}])

    await _worker(store, bank).run_cycle()

    assert store.native_calls == [EFFECT_OPERATION_ID, EFFECT_OPERATION_ID]
    assert bank.release_calls == [
        {
            "escrow_id": ESCROW_ID,
            "recipient_account_id": WORKER_ACCOUNT_ID,
            "operation_id": EFFECT_OPERATION_ID,
            "source_reference": f"task:{TASK_ID}:approved",
        }
    ]
    assert bank.split_calls == []


async def test_split_dispatch_uses_only_the_persisted_immutable_intent() -> None:
    request = split_request()
    lease = lease_envelope(
        effect_kind="bank_split",
        financial_request=request,
    )
    pending = diagnostic_envelope(state="leased", effect_kind="bank_split")
    completed = diagnostic_envelope(effect_kind="bank_split")
    store = RecoveryStore(
        leases=[lease, empty_lease_envelope()],
        finalize_native=[pending, completed],
    )
    bank = RecoveryBank(split_outcomes=[{"untrusted_ack": True}])

    await _worker(store, bank).run_cycle()

    assert bank.split_calls == [
        {
            "escrow_id": ESCROW_ID,
            "worker_account_id": WORKER_ACCOUNT_ID,
            "poster_account_id": POSTER_ID,
            "worker_pct": 60,
            "operation_id": EFFECT_OPERATION_ID,
            "source_reference": f"task:{TASK_ID}:ruling:clm-mp08",
        }
    ]
    assert bank.release_calls == []


@pytest.mark.parametrize(
    ("outcome", "expected_code"),
    [
        (
            ServiceError(
                "central_bank_unavailable",
                "bounded service error",
                503,
                {},
            ),
            "central_bank_unavailable",
        ),
        (TimeoutError("private timeout text"), "request_timeout"),
        (RuntimeError("private unexpected text"), "unexpected_recovery_error"),
    ],
)
async def test_delivery_failures_are_contained_and_mapped_to_finite_codes(
    outcome: BaseException,
    expected_code: str,
) -> None:
    pending = diagnostic_envelope(state="leased")
    failed = diagnostic_envelope(state="pending")
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[pending],
        failures=[failed],
    )
    bank = RecoveryBank(release_outcomes=[outcome])

    processed = await _worker(store, bank).run_cycle()

    assert processed == 1
    assert store.failure_calls == [(EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, expected_code)]


async def test_gateway_transport_failure_is_reported_without_bank_dispatch() -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[ServiceError("gateway_unavailable", "private gateway failure", 503, {})],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "gateway_unavailable")
    ]
    assert bank.release_calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        "envelope_extra",
        "protocol_version",
        "delivery_extra",
        "missing_task",
        "bad_source_kind",
        "bad_effect_kind",
        "wrong_worker",
        "bool_generation",
        "bool_attempt_count",
        "missing_intent",
        "intent_extra",
        "wrong_hash",
        "wrong_escrow_task",
        "wrong_effect_identity",
    ],
)
async def test_malformed_lease_matrix_never_dispatches_bank_and_is_contained(
    mutation: str,
) -> None:
    malformed = lease_envelope()
    delivery = malformed["delivery"]
    assert isinstance(delivery, dict)
    intent = delivery["effect_intent"]
    assert isinstance(intent, dict)
    escrow = intent["expected_escrow"]
    assert isinstance(escrow, dict)
    if mutation == "envelope_extra":
        malformed["extra"] = True
    elif mutation == "protocol_version":
        malformed["protocol_version"] = 2
    elif mutation == "delivery_extra":
        delivery["extra"] = True
    elif mutation == "missing_task":
        del delivery["task_id"]
    elif mutation == "bad_source_kind":
        delivery["source_kind"] = "court_claim"
    elif mutation == "bad_effect_kind":
        delivery["effect_kind"] = "court_claim"
    elif mutation == "wrong_worker":
        delivery["worker_id"] = "recovery-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    elif mutation == "bool_generation":
        delivery["lease_generation"] = True
    elif mutation == "bool_attempt_count":
        delivery["total_attempt_count"] = True
    elif mutation == "missing_intent":
        del delivery["effect_intent"]
    elif mutation == "intent_extra":
        intent["extra"] = True
    elif mutation == "wrong_hash":
        intent["request_sha256"] = "f" * 64
    elif mutation == "wrong_escrow_task":
        escrow["task_id"] = "t-other"
    elif mutation == "wrong_effect_identity":
        delivery["effect_operation_id"] = "op-22222222-2222-4222-8222-222222222222"
    else:
        raise AssertionError(f"Unknown mutation: {mutation}")
    store = RecoveryStore(
        leases=[malformed, empty_lease_envelope()],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1

    assert bank.release_calls == []
    assert bank.split_calls == []
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "invalid_gateway_response")
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        "envelope_extra",
        "protocol_version",
        "delivery_extra",
        "missing_state",
        "state_type",
        "wrong_identity",
        "wrong_task",
        "wrong_source",
        "wrong_effect_kind",
        "confirmed_without_reference",
    ],
)
async def test_malformed_finalize_matrix_is_reported_without_second_bank_dispatch(
    mutation: str,
) -> None:
    changed = copy.deepcopy(diagnostic_envelope())
    delivery = changed["delivery"]
    assert isinstance(delivery, dict)
    if mutation == "envelope_extra":
        changed["extra"] = True
    elif mutation == "protocol_version":
        changed["protocol_version"] = 2
    elif mutation == "delivery_extra":
        delivery["extra"] = True
    elif mutation == "missing_state":
        del delivery["delivery_state"]
    elif mutation == "state_type":
        delivery["delivery_state"] = 1
    elif mutation == "wrong_identity":
        delivery["effect_operation_id"] = "op-22222222-2222-4222-8222-222222222222"
    elif mutation == "wrong_task":
        delivery["task_id"] = "t-other"
    elif mutation == "wrong_source":
        delivery["source_id"] = "op-22222222-2222-4222-8222-222222222222"
    elif mutation == "wrong_effect_kind":
        delivery["effect_kind"] = "bank_split"
    elif mutation == "confirmed_without_reference":
        delivery["receipt_reference"] = None
    else:
        raise AssertionError(f"Unknown mutation: {mutation}")
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[changed],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1

    assert bank.release_calls == []
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "invalid_gateway_response")
    ]


async def test_legacy_succeeded_history_is_projection_only_and_never_dispatched() -> None:
    observation_id = "obs-11111111-1111-4111-8111-111111111111"
    lease = lease_envelope(
        source_kind="legacy_settlement_observation",
        source_id=observation_id,
    )
    completed = diagnostic_envelope(
        source_kind="legacy_settlement_observation",
        source_id=observation_id,
    )
    store = RecoveryStore(
        leases=[lease, empty_lease_envelope()],
        finalize_legacy=[GatewayResult(status_code=200, body=completed)],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert store.legacy_calls == [observation_id]
    assert bank.release_calls == []


async def test_legacy_pending_dispatches_only_after_authoritative_202() -> None:
    observation_id = "obs-11111111-1111-4111-8111-111111111111"
    lease = lease_envelope(
        source_kind="legacy_settlement_observation",
        source_id=observation_id,
    )
    pending = diagnostic_envelope(
        state="leased",
        source_kind="legacy_settlement_observation",
        source_id=observation_id,
    )
    completed = diagnostic_envelope(
        source_kind="legacy_settlement_observation",
        source_id=observation_id,
    )
    store = RecoveryStore(
        leases=[lease, empty_lease_envelope()],
        finalize_legacy=[
            GatewayResult(status_code=202, body=pending),
            GatewayResult(status_code=200, body=completed),
        ],
    )
    bank = RecoveryBank(release_outcomes=[{"untrusted_ack": True}])

    assert await _worker(store, bank).run_cycle() == 1
    assert store.legacy_calls == [observation_id, observation_id]
    assert bank.release_calls == [
        {
            "escrow_id": ESCROW_ID,
            "recipient_account_id": WORKER_ACCOUNT_ID,
            "operation_id": EFFECT_OPERATION_ID,
            "source_reference": f"task:{TASK_ID}:approved",
        }
    ]


@pytest.mark.parametrize("status_code", [200, 202])
@pytest.mark.parametrize(
    "mutation",
    [
        "envelope_extra",
        "protocol_version",
        "delivery_extra",
        "wrong_observation",
        "wrong_task",
        "wrong_source_kind",
        "wrong_effect_kind",
        "wrong_effect_identity",
        "wrong_state",
        "state_type",
    ],
)
async def test_malformed_legacy_finalize_matrix_never_dispatches_bank(
    status_code: int,
    mutation: str,
) -> None:
    observation_id = "obs-11111111-1111-4111-8111-111111111111"
    lease = lease_envelope(
        source_kind="legacy_settlement_observation",
        source_id=observation_id,
    )
    body = diagnostic_envelope(
        state="leased" if status_code == 202 else "completed",
        source_kind="legacy_settlement_observation",
        source_id=observation_id,
    )
    delivery = body["delivery"]
    assert isinstance(delivery, dict)
    if mutation == "envelope_extra":
        body["extra"] = True
    elif mutation == "protocol_version":
        body["protocol_version"] = 2
    elif mutation == "delivery_extra":
        delivery["extra"] = True
    elif mutation == "wrong_observation":
        delivery["source_id"] = "obs-22222222-2222-4222-8222-222222222222"
    elif mutation == "wrong_task":
        delivery["task_id"] = "t-other"
    elif mutation == "wrong_source_kind":
        delivery["source_kind"] = "native_task_operation"
    elif mutation == "wrong_effect_kind":
        delivery["effect_kind"] = "bank_split"
    elif mutation == "wrong_effect_identity":
        delivery["effect_operation_id"] = "op-22222222-2222-4222-8222-222222222222"
    elif mutation == "wrong_state":
        delivery["delivery_state"] = "pending"
    elif mutation == "state_type":
        delivery["delivery_state"] = 1
    else:
        raise AssertionError(f"Unknown mutation: {mutation}")
    store = RecoveryStore(
        leases=[lease, empty_lease_envelope()],
        finalize_legacy=[GatewayResult(status_code=status_code, body=body)],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert bank.release_calls == []
    assert bank.split_calls == []
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "invalid_gateway_response")
    ]


async def test_legacy_finalize_conflict_is_contained_without_bank_or_failure_rewrite() -> None:
    observation_id = "obs-11111111-1111-4111-8111-111111111111"
    store = RecoveryStore(
        leases=[
            lease_envelope(
                source_kind="legacy_settlement_observation",
                source_id=observation_id,
            ),
            empty_lease_envelope(),
        ],
        finalize_legacy=[ServiceError("effect_evidence_conflict", "bounded conflict", 409, {})],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert bank.release_calls == []
    assert bank.split_calls == []
    assert store.failure_calls == []


async def test_cycle_limit_is_a_hard_bound_on_leased_deliveries() -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), lease_envelope(), empty_lease_envelope()],
        finalize_native=[diagnostic_envelope(), diagnostic_envelope()],
    )

    processed = await _worker(store, RecoveryBank(), cycle_limit=1).run_cycle()

    assert processed == 1
    assert store.lease_calls == [RECOVERY_WORKER_ID]

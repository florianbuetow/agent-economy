"""Adversarial MP-08 dispatch fences at the observable worker boundary."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from task_board_service.services.financial_effect_recovery import FinancialEffectRecovery
from task_board_service.task_operation_contract import canonical_hash
from tests.fakes.board_mp08_recovery import (
    EFFECT_OPERATION_ID,
    ESCROW_ID,
    RECOVERY_WORKER_ID,
    TASK_ID,
    WORKER_ACCOUNT_ID,
    GatewayResult,
    RecoveryBank,
    RecoveryStore,
    diagnostic_envelope,
    empty_lease_envelope,
    lease_envelope,
    release_request,
    split_request,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
_OBSERVATION_ID = "obs-11111111-1111-4111-8111-111111111111"


def _worker(store: RecoveryStore, bank: RecoveryBank, *, limit: int = 2) -> FinancialEffectRecovery:
    return FinancialEffectRecovery(
        store=store,
        bank=bank,
        worker_id=RECOVERY_WORKER_ID,
        max_delivery_attempts_per_cycle=limit,
    )


def _changed_leased_diagnostic(mutation: str) -> dict[str, Any]:
    envelope = copy.deepcopy(diagnostic_envelope(state="leased"))
    delivery = envelope["delivery"]
    assert isinstance(delivery, dict)
    if mutation == "worker":
        delivery["lease_owner_id"] = "recovery-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    elif mutation == "generation":
        delivery["lease_generation"] = 4
    elif mutation == "total_attempts":
        delivery["total_attempt_count"] = 4
    elif mutation == "cycle_attempts":
        delivery["cycle_attempt_count"] = 3
    elif mutation == "acquired":
        delivery["lease_acquired_at_us"] = 1_000_001
    elif mutation == "expires":
        delivery["lease_expires_at_us"] = 46_000_001
    elif mutation == "observed_at_expiry":
        delivery["observed_at_us"] = 46_000_000
    elif mutation == "next_attempt":
        delivery["next_attempt_at_us"] = 3_000_000
    elif mutation == "evidence_schedule":
        delivery["next_evidence_check_at_us"] = 3_000_000
    elif mutation == "receipt":
        delivery["receipt_reference"] = EFFECT_OPERATION_ID
    else:
        raise AssertionError(f"Unknown mutation: {mutation}")
    return envelope


@pytest.mark.parametrize(
    "mutation",
    [
        "worker",
        "generation",
        "total_attempts",
        "cycle_attempts",
        "acquired",
        "expires",
        "observed_at_expiry",
        "next_attempt",
        "evidence_schedule",
        "receipt",
    ],
)
async def test_predispatch_diagnostic_must_match_the_exact_lease_fence(
    mutation: str,
) -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[_changed_leased_diagnostic(mutation)],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert bank.release_calls == []
    assert bank.split_calls == []
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "invalid_gateway_response")
    ]


async def test_exact_predispatch_fence_is_the_positive_dispatch_control() -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[diagnostic_envelope(state="leased"), diagnostic_envelope()],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert bank.release_calls == [
        {
            "escrow_id": ESCROW_ID,
            "recipient_account_id": WORKER_ACCOUNT_ID,
            "operation_id": EFFECT_OPERATION_ID,
            "source_reference": f"task:{TASK_ID}:approved",
        }
    ]


async def test_legacy_bank_split_lease_is_rejected_before_any_dispatch() -> None:
    request = split_request()
    lease = lease_envelope(
        source_kind="legacy_settlement_observation",
        source_id=_OBSERVATION_ID,
        effect_kind="bank_split",
        financial_request=request,
    )
    store = RecoveryStore(
        leases=[lease, empty_lease_envelope()],
        finalize_legacy=[
            GatewayResult(
                status_code=202,
                body=diagnostic_envelope(
                    state="leased",
                    source_kind="legacy_settlement_observation",
                    source_id=_OBSERVATION_ID,
                    effect_kind="bank_split",
                ),
            ),
            GatewayResult(
                status_code=200,
                body=diagnostic_envelope(
                    source_kind="legacy_settlement_observation",
                    source_id=_OBSERVATION_ID,
                    effect_kind="bank_split",
                ),
            ),
        ],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert bank.release_calls == []
    assert bank.split_calls == []
    assert store.legacy_calls == []
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "invalid_gateway_response")
    ]


async def test_legacy_release_requires_the_exact_approved_source_reference() -> None:
    request = release_request()
    request["source_reference"] = f"task:{TASK_ID}:cancelled"
    lease = lease_envelope(
        source_kind="legacy_settlement_observation",
        source_id=_OBSERVATION_ID,
        financial_request=request,
    )
    intent = lease["delivery"]["effect_intent"]
    assert isinstance(intent, dict)
    intent["request_sha256"] = canonical_hash(request)
    store = RecoveryStore(
        leases=[lease, empty_lease_envelope()],
        finalize_legacy=[
            GatewayResult(
                status_code=202,
                body=diagnostic_envelope(
                    state="leased",
                    source_kind="legacy_settlement_observation",
                    source_id=_OBSERVATION_ID,
                ),
            ),
            GatewayResult(
                status_code=200,
                body=diagnostic_envelope(
                    source_kind="legacy_settlement_observation",
                    source_id=_OBSERVATION_ID,
                ),
            ),
        ],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert bank.release_calls == []
    assert bank.split_calls == []
    assert store.legacy_calls == []
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "invalid_gateway_response")
    ]


async def test_exact_legacy_release_is_the_positive_control() -> None:
    lease = lease_envelope(
        source_kind="legacy_settlement_observation",
        source_id=_OBSERVATION_ID,
    )
    pending = diagnostic_envelope(
        state="leased",
        source_kind="legacy_settlement_observation",
        source_id=_OBSERVATION_ID,
    )
    completed = diagnostic_envelope(
        source_kind="legacy_settlement_observation",
        source_id=_OBSERVATION_ID,
    )
    store = RecoveryStore(
        leases=[lease, empty_lease_envelope()],
        finalize_legacy=[
            GatewayResult(status_code=202, body=pending),
            GatewayResult(status_code=200, body=completed),
        ],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert bank.release_calls == [
        {
            "escrow_id": ESCROW_ID,
            "recipient_account_id": WORKER_ACCOUNT_ID,
            "operation_id": EFFECT_OPERATION_ID,
            "source_reference": f"task:{TASK_ID}:approved",
        }
    ]

"""MP-08 temporal fences, failure acknowledgments, and timeout classification."""

from __future__ import annotations

import copy
import logging
from typing import Any

import httpx
import pytest

from task_board_service.services.financial_effect_recovery import FinancialEffectRecovery
from tests.fakes.board_mp08_recovery import (
    EFFECT_OPERATION_ID,
    RECOVERY_WORKER_ID,
    SECOND_EFFECT_OPERATION_ID,
    RecoveryBank,
    RecoveryStore,
    diagnostic_envelope,
    empty_lease_envelope,
    lease_envelope,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _worker(store: RecoveryStore, bank: RecoveryBank, *, limit: int = 2) -> FinancialEffectRecovery:
    return FinancialEffectRecovery(
        store=store,
        bank=bank,
        worker_id=RECOVERY_WORKER_ID,
        max_delivery_attempts_per_cycle=limit,
    )


def _second_lease() -> dict[str, Any]:
    return lease_envelope(
        effect_operation_id=SECOND_EFFECT_OPERATION_ID,
        source_id=SECOND_EFFECT_OPERATION_ID,
    )


def _second_completed() -> dict[str, Any]:
    return diagnostic_envelope(
        effect_operation_id=SECOND_EFFECT_OPERATION_ID,
        source_id=SECOND_EFFECT_OPERATION_ID,
    )


@pytest.mark.parametrize(
    "mutation",
    ["cycle_exceeds_total", "observed_before_acquired", "acquired_before_enqueue"],
)
async def test_temporally_impossible_lease_or_predispatch_diagnostic_never_reaches_bank(
    mutation: str,
) -> None:
    lease = lease_envelope()
    lease_delivery = lease["delivery"]
    assert isinstance(lease_delivery, dict)
    diagnostic = copy.deepcopy(diagnostic_envelope(state="leased"))
    diagnostic_delivery = diagnostic["delivery"]
    assert isinstance(diagnostic_delivery, dict)
    if mutation == "cycle_exceeds_total":
        lease_delivery["cycle_attempt_count"] = 4
        diagnostic_delivery["cycle_attempt_count"] = 4
    elif mutation == "observed_before_acquired":
        diagnostic_delivery["observed_at_us"] = 999_999
    elif mutation == "acquired_before_enqueue":
        diagnostic_delivery["enqueued_at_us"] = 1_000_001
    else:
        raise AssertionError(f"Unknown mutation: {mutation}")
    store = RecoveryStore(
        leases=[lease, empty_lease_envelope()],
        finalize_native=[diagnostic],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank()

    assert await _worker(store, bank).run_cycle() == 1
    assert bank.release_calls == []
    assert bank.split_calls == []
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "invalid_gateway_response")
    ]


def _assert_invalid_ack_log(caplog: pytest.LogCaptureFixture) -> None:
    warning_records = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert warning_records, "invalid failure acknowledgment was silently accepted"
    assert any("invalid_gateway_response" in record.__dict__.values() for record in warning_records)
    rendered = " ".join(
        value
        for record in warning_records
        for value in (
            record.getMessage(),
            *(str(item) for item in record.__dict__.values()),
        )
    )
    assert "secret-invalid-ack" not in rendered


def _failure_store(acknowledgment: dict[str, Any]) -> RecoveryStore:
    return RecoveryStore(
        leases=[lease_envelope(), _second_lease(), empty_lease_envelope()],
        finalize_native=[
            diagnostic_envelope(state="leased"),
            diagnostic_envelope(state="leased"),
            _second_completed(),
        ],
        failures=[acknowledgment],
    )


async def test_integrity_blocked_failure_ack_must_be_409_not_silent_200(
    caplog: pytest.LogCaptureFixture,
) -> None:
    acknowledgment = diagnostic_envelope(state="integrity_blocked")
    delivery = acknowledgment["delivery"]
    assert isinstance(delivery, dict)
    delivery["last_failure_code"] = "secret-invalid-ack"
    store = _failure_store(acknowledgment)

    assert (
        await _worker(
            store,
            RecoveryBank(release_outcomes=[RuntimeError("trigger")]),
        ).run_cycle()
        == 2
    )
    assert store.native_calls == [
        EFFECT_OPERATION_ID,
        EFFECT_OPERATION_ID,
        SECOND_EFFECT_OPERATION_ID,
    ]
    _assert_invalid_ack_log(caplog)


@pytest.mark.parametrize("mutation", ["next_attempt", "evidence_schedule", "lease_fields"])
async def test_contradictory_completed_failure_ack_is_visible_and_later_work_continues(
    mutation: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    acknowledgment = copy.deepcopy(diagnostic_envelope())
    delivery = acknowledgment["delivery"]
    assert isinstance(delivery, dict)
    if mutation == "next_attempt":
        delivery["next_attempt_at_us"] = 2_000_000
    elif mutation == "evidence_schedule":
        delivery["next_evidence_check_at_us"] = 3_000_000
    elif mutation == "lease_fields":
        delivery["lease_owner_id"] = RECOVERY_WORKER_ID
        delivery["lease_acquired_at_us"] = 1_000_000
        delivery["lease_expires_at_us"] = 46_000_000
    else:
        raise AssertionError(f"Unknown mutation: {mutation}")
    delivery["last_failure_code"] = "secret-invalid-ack"
    store = _failure_store(acknowledgment)

    assert (
        await _worker(
            store,
            RecoveryBank(release_outcomes=[RuntimeError("trigger")]),
        ).run_cycle()
        == 2
    )
    assert store.native_calls[-1] == SECOND_EFFECT_OPERATION_ID
    _assert_invalid_ack_log(caplog)


class MisleadingTimeoutError(RuntimeError):
    """An unrelated failure whose class name must not grant timeout semantics."""


def _unrelated_error_with_timeout_context() -> RuntimeError:
    try:
        raise TimeoutError("old-timeout-context")
    except TimeoutError:
        try:
            raise RuntimeError("later-unrelated-failure")
        except RuntimeError as error:
            return error


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (MisleadingTimeoutError("not-a-timeout"), "unexpected_recovery_error"),
        (_unrelated_error_with_timeout_context(), "unexpected_recovery_error"),
        (TimeoutError("actual-timeout"), "request_timeout"),
        (
            httpx.ReadTimeout(
                "actual-http-timeout",
                request=httpx.Request("POST", "http://bank.invalid/release"),
            ),
            "request_timeout",
        ),
    ],
)
async def test_timeout_classification_uses_exception_type_or_explicit_cause_only(
    error: BaseException,
    expected_code: str,
) -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[diagnostic_envelope(state="leased")],
        failures=[diagnostic_envelope(state="pending")],
    )

    assert await _worker(store, RecoveryBank(release_outcomes=[error])).run_cycle() == 1
    assert store.failure_calls == [(EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, expected_code)]

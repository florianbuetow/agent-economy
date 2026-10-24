"""Observable MP-08 worker diagnostics and dependency-origin classification."""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from service_commons.exceptions import ServiceError

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
_FINITE_CODES = {
    "central_bank_unavailable",
    "gateway_unavailable",
    "invalid_gateway_response",
    "request_timeout",
    "unexpected_recovery_error",
}


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


def _assert_bounded_log(
    caplog: pytest.LogCaptureFixture,
    expected_code: str,
    secret: str,
) -> None:
    assert expected_code in _FINITE_CODES
    warning_records = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert warning_records, "non-stale failure-report loss was silent"
    assert any(expected_code in record.__dict__.values() for record in warning_records)
    rendered = " ".join(
        value
        for record in warning_records
        for value in (
            record.getMessage(),
            *(str(item) for item in record.__dict__.values()),
        )
    )
    assert secret not in rendered


class _RaisingReportStore(RecoveryStore):
    def __init__(self, report_error: BaseException) -> None:
        super().__init__(
            leases=[lease_envelope(), _second_lease(), empty_lease_envelope()],
            finalize_native=[
                diagnostic_envelope(state="leased"),
                diagnostic_envelope(state="leased"),
                _second_completed(),
            ],
        )
        self.report_error = report_error

    def fail_task_effect_delivery(
        self,
        effect_operation_id: str,
        lease_generation: int,
        worker_id: str,
        error_code: str,
    ) -> dict[str, Any]:
        self.failure_calls.append((effect_operation_id, lease_generation, worker_id, error_code))
        raise self.report_error


@pytest.mark.parametrize(
    ("report_error", "expected_code", "secret"),
    [
        (
            ServiceError("gateway_unavailable", "secret-service-report", 503, {}),
            "gateway_unavailable",
            "secret-service-report",
        ),
        (
            httpx.ConnectError(
                "secret-http-report",
                request=httpx.Request("POST", "http://gateway.invalid/fail"),
            ),
            "gateway_unavailable",
            "secret-http-report",
        ),
        (
            ServiceError(
                "effect_delivery_attempt_stale",
                "secret-wrong-status-stale",
                500,
                {},
            ),
            "gateway_unavailable",
            "secret-wrong-status-stale",
        ),
        (
            ServiceError("other_conflict", "secret-other-conflict", 409, {}),
            "gateway_unavailable",
            "secret-other-conflict",
        ),
        (
            RuntimeError("secret-unexpected-report"),
            "unexpected_recovery_error",
            "secret-unexpected-report",
        ),
    ],
)
async def test_nonstale_failure_report_errors_are_bounded_visible_and_do_not_starve(
    report_error: BaseException,
    expected_code: str,
    secret: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = _RaisingReportStore(report_error)
    bank = RecoveryBank(release_outcomes=[RuntimeError("secret-bank-trigger")])

    assert await _worker(store, bank).run_cycle() == 2
    assert store.native_calls == [
        EFFECT_OPERATION_ID,
        EFFECT_OPERATION_ID,
        SECOND_EFFECT_OPERATION_ID,
    ]
    _assert_bounded_log(caplog, expected_code, secret)


async def test_malformed_failure_report_is_visible_bounded_and_later_work_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), _second_lease(), empty_lease_envelope()],
        finalize_native=[
            diagnostic_envelope(state="leased"),
            diagnostic_envelope(state="leased"),
            _second_completed(),
        ],
        failures=[{"protocol_version": 1, "private": "secret-malformed-report"}],
    )
    bank = RecoveryBank(release_outcomes=[RuntimeError("trigger")])

    assert await _worker(store, bank).run_cycle() == 2
    assert store.native_calls == [
        EFFECT_OPERATION_ID,
        EFFECT_OPERATION_ID,
        SECOND_EFFECT_OPERATION_ID,
    ]
    _assert_bounded_log(caplog, "invalid_gateway_response", "secret-malformed-report")


@pytest.mark.parametrize(
    "report_outcome",
    [
        ServiceError("effect_delivery_attempt_stale", "stale", 409, {}),
        ServiceError("effect_evidence_conflict", "conflict", 409, {}),
        diagnostic_envelope(),
    ],
)
async def test_exact_stale_conflict_or_completed_failure_report_is_quiet(
    report_outcome: dict[str, Any] | ServiceError,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[
            diagnostic_envelope(state="leased"),
            diagnostic_envelope(state="leased"),
        ],
        failures=[report_outcome],
    )
    bank = RecoveryBank(release_outcomes=[RuntimeError("trigger")])

    assert await _worker(store, bank).run_cycle() == 1
    assert [record for record in caplog.records if record.levelno >= logging.WARNING] == []


async def test_invalid_lease_without_failure_identity_is_logged_and_later_work_continues(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store = RecoveryStore(
        leases=[
            {"protocol_version": 1, "delivery": {"private": "secret-invalid-lease"}},
            _second_lease(),
            empty_lease_envelope(),
        ],
        finalize_native=[_second_completed()],
    )

    assert await _worker(store, RecoveryBank()).run_cycle() == 2
    assert store.native_calls == [SECOND_EFFECT_OPERATION_ID]
    _assert_bounded_log(caplog, "invalid_gateway_response", "secret-invalid-lease")


class _GatewayTransportStore(RecoveryStore):
    def finalize_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]:
        self.native_calls.append(effect_operation_id)
        raise httpx.ConnectError(
            "secret-gateway-transport",
            request=httpx.Request("POST", "http://gateway.invalid/finalize"),
        )


async def test_gateway_transport_origin_maps_to_gateway_unavailable_not_bank() -> None:
    store = _GatewayTransportStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        failures=[diagnostic_envelope(state="pending")],
    )

    assert await _worker(store, RecoveryBank()).run_cycle() == 1
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "gateway_unavailable")
    ]


def _wrapped_timeout() -> ServiceError:
    error = ServiceError("central_bank_unavailable", "secret-wrapped-timeout", 503, {})
    error.__cause__ = TimeoutError("secret-timeout-cause")
    return error


@pytest.mark.parametrize(
    "bank_error",
    [
        httpx.ReadTimeout(
            "secret-http-timeout",
            request=httpx.Request("POST", "http://bank.invalid/release"),
        ),
        _wrapped_timeout(),
    ],
)
async def test_bank_timeout_and_wrapped_timeout_causes_map_to_request_timeout(
    bank_error: BaseException,
) -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[diagnostic_envelope(state="leased")],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank(release_outcomes=[bank_error])

    assert await _worker(store, bank).run_cycle() == 1
    assert store.failure_calls == [(EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "request_timeout")]


async def test_non_timeout_bank_transport_remains_central_bank_unavailable() -> None:
    store = RecoveryStore(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[diagnostic_envelope(state="leased")],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank(
        release_outcomes=[
            httpx.ConnectError(
                "secret-bank-connect",
                request=httpx.Request("POST", "http://bank.invalid/release"),
            )
        ]
    )

    assert await _worker(store, bank).run_cycle() == 1
    assert store.failure_calls == [
        (EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, "central_bank_unavailable")
    ]

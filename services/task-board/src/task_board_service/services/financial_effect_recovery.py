"""Bounded, receipt-first recovery for persisted task financial effects."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from functools import partial
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from service_commons.exceptions import ServiceError

from task_board_service.effect_recovery_contract import (
    EffectDeliveryLease,
    ReleaseRequest,
    SettlementRequest,
    failure_identity,
    invalid_gateway_response,
    parse_diagnostic_envelope,
    parse_failure_envelope,
    parse_lease_envelope,
    validate_recovery_worker_id,
)
from task_board_service.services.task_db_client import PlatformHttpError, PlatformHttpTimeoutError

if TYPE_CHECKING:
    from collections.abc import Callable


class RecoveryStore(Protocol):
    """Protected Gateway operations needed by the recovery worker."""

    def lease_task_effect_delivery(self, worker_id: str) -> dict[str, Any]: ...

    def finalize_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]: ...

    def finalize_legacy_effect_observation(self, observation_id: str) -> RecoveryFinalizeResult: ...

    def fail_task_effect_delivery(
        self,
        effect_operation_id: str,
        lease_generation: int,
        worker_id: str,
        error_code: str,
    ) -> dict[str, Any]: ...


class RecoveryBank(Protocol):
    """Canonical Central Bank calls used by persisted financial intents."""

    async def escrow_release(self, **terms: object) -> object: ...

    async def escrow_split(self, **terms: object) -> object: ...

    async def settle_escrow(self, **terms: object) -> object: ...


class RecoveryFinalizeResult(Protocol):
    """Status and parsed body returned by the protected legacy route."""

    status_code: int
    body: dict[str, Any]


async def _storage_call[T](callback: Callable[[], T]) -> T:
    """Run synchronous Gateway I/O off-loop and drain it on cancellation."""
    task = asyncio.create_task(asyncio.to_thread(callback))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise


class FinancialEffectRecovery:
    """Lease bounded work and converge each effect from authoritative receipts."""

    def __init__(
        self,
        *,
        store: RecoveryStore,
        bank: RecoveryBank,
        worker_id: str | None,
        max_delivery_attempts_per_cycle: int,
    ) -> None:
        if type(max_delivery_attempts_per_cycle) is not int or max_delivery_attempts_per_cycle <= 0:
            raise ValueError("max_delivery_attempts_per_cycle must be a positive integer")
        self._store = store
        self._bank = bank
        candidate_worker_id = worker_id or f"recovery-{uuid4()}"
        self.worker_id = validate_recovery_worker_id(candidate_worker_id)
        self._max_delivery_attempts_per_cycle = max_delivery_attempts_per_cycle

    async def run_cycle(self) -> int:
        """Process at most the configured number of leased deliveries."""
        processed = 0
        while processed < self._max_delivery_attempts_per_cycle:
            raw_lease = await _storage_call(
                partial(self._store.lease_task_effect_delivery, self.worker_id)
            )
            try:
                envelope = parse_lease_envelope(raw_lease)
                lease = envelope.delivery
                if lease is None:
                    break
                if lease.worker_id != self.worker_id:
                    raise invalid_gateway_response()
            except ServiceError:
                processed += 1
                identity = failure_identity(raw_lease)
                if identity is not None:
                    await self._report_failure(identity[0], identity[1], "invalid_gateway_response")
                else:
                    self._log_failure(
                        "invalid_gateway_response",
                        stage="lease_validation",
                        effect_operation_id=None,
                    )
                continue

            processed += 1
            await self._process_delivery(lease)
        return processed

    async def _process_delivery(self, lease: EffectDeliveryLease) -> None:
        try:
            state = await self._finalize(lease)
        except ServiceError as exc:
            if self._is_evidence_conflict(exc):
                return
            await self._report_failure(
                lease.effect_operation_id,
                lease.lease_generation,
                self._gateway_exception_code(exc),
            )
            return
        except PlatformHttpError as exc:
            await self._report_failure(
                lease.effect_operation_id,
                lease.lease_generation,
                self._gateway_exception_code(exc),
            )
            return
        except Exception as exc:
            await self._report_failure(
                lease.effect_operation_id,
                lease.lease_generation,
                self._gateway_exception_code(exc),
            )
            return

        if state == "completed":
            return

        bank_error: BaseException | None = None
        try:
            await self._dispatch(lease)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            bank_error = exc

        post_dispatch_error: BaseException | None = None
        try:
            state = await self._finalize(lease)
            if state == "completed":
                return
            if bank_error is None:
                post_dispatch_error = invalid_gateway_response()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if isinstance(exc, ServiceError) and self._is_evidence_conflict(exc):
                return
            post_dispatch_error = exc

        if post_dispatch_error is not None and (
            bank_error is None or isinstance(post_dispatch_error, (ServiceError, PlatformHttpError))
        ):
            error_code = self._gateway_exception_code(post_dispatch_error)
        else:
            error_code = self._bank_exception_code(bank_error)

        await self._report_failure(
            lease.effect_operation_id,
            lease.lease_generation,
            error_code,
        )

    async def _finalize(self, lease: EffectDeliveryLease) -> str:
        if lease.source_kind == "native_task_operation":
            body = await _storage_call(
                partial(self._store.finalize_task_effect_delivery, lease.effect_operation_id)
            )
            diagnostic = parse_diagnostic_envelope(body, lease=lease, required_state=None)
            if diagnostic.delivery_state not in {"leased", "completed"}:
                raise invalid_gateway_response()
            return diagnostic.delivery_state

        result = await _storage_call(
            partial(self._store.finalize_legacy_effect_observation, lease.source_id)
        )
        if result.status_code == 200:
            parse_diagnostic_envelope(result.body, lease=lease, required_state="completed")
            return "completed"
        if result.status_code == 202:
            parse_diagnostic_envelope(result.body, lease=lease, required_state="leased")
            return "leased"
        raise invalid_gateway_response()

    async def _dispatch(self, lease: EffectDeliveryLease) -> None:
        request = lease.effect_intent.financial_request
        if isinstance(request, ReleaseRequest):
            await self._bank.escrow_release(
                escrow_id=request.escrow_id,
                recipient_account_id=request.recipient_account_id,
                operation_id=lease.effect_operation_id,
                source_reference=request.source_reference,
            )
            return
        if isinstance(request, SettlementRequest):
            await self._bank.settle_escrow(
                escrow_id=request.escrow_id,
                worker_account_id=request.worker_account_id,
                poster_account_id=request.poster_account_id,
                worker_amount=request.worker_amount,
                operation_id=lease.effect_operation_id,
                source_reference=request.source_reference,
            )
            return
        await self._bank.escrow_split(
            escrow_id=request.escrow_id,
            worker_account_id=request.worker_account_id,
            poster_account_id=request.poster_account_id,
            worker_pct=request.worker_pct,
            operation_id=lease.effect_operation_id,
            source_reference=request.source_reference,
        )

    async def _report_failure(
        self,
        effect_operation_id: str,
        lease_generation: int,
        error_code: str,
    ) -> None:
        try:
            body = await _storage_call(
                partial(
                    self._store.fail_task_effect_delivery,
                    effect_operation_id,
                    lease_generation,
                    self.worker_id,
                    error_code,
                )
            )
            parse_failure_envelope(body, effect_operation_id=effect_operation_id)
        except ServiceError as exc:
            if self._is_quiet_failure_report_error(exc):
                return
            self._log_failure(
                self._gateway_exception_code(exc),
                stage="failure_report",
                effect_operation_id=effect_operation_id,
            )
        except PlatformHttpError as exc:
            self._log_failure(
                self._gateway_exception_code(exc),
                stage="failure_report",
                effect_operation_id=effect_operation_id,
            )
        except Exception as exc:
            self._log_failure(
                self._gateway_exception_code(exc),
                stage="failure_report",
                effect_operation_id=effect_operation_id,
            )

    @staticmethod
    def _is_evidence_conflict(error: ServiceError) -> bool:
        return error.error == "effect_evidence_conflict" and error.status_code == 409

    @staticmethod
    def _is_quiet_failure_report_error(error: ServiceError) -> bool:
        return error.status_code == 409 and error.error in {
            "effect_delivery_attempt_stale",
            "effect_evidence_conflict",
        }

    @classmethod
    def _gateway_exception_code(cls, error: BaseException) -> str:
        if cls._has_timeout_cause(error):
            return "request_timeout"
        if isinstance(error, ServiceError) and error.error == "invalid_gateway_response":
            return "invalid_gateway_response"
        if isinstance(error, (ServiceError, PlatformHttpError)):
            return "gateway_unavailable"
        return "unexpected_recovery_error"

    @classmethod
    def _bank_exception_code(cls, error: BaseException | None) -> str:
        if error is not None and cls._has_timeout_cause(error):
            return "request_timeout"
        if isinstance(error, PlatformHttpError):
            return "central_bank_unavailable"
        if isinstance(error, ServiceError):
            if error.error == "invalid_gateway_response":
                return "invalid_gateway_response"
            if error.error == "gateway_unavailable":
                return "gateway_unavailable"
            return "central_bank_unavailable"
        return "unexpected_recovery_error"

    @staticmethod
    def _has_timeout_cause(error: BaseException) -> bool:
        current: BaseException | None = error
        visited: set[int] = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            if isinstance(current, (TimeoutError, PlatformHttpTimeoutError)):
                return True
            current = current.__cause__
        return False

    async def run_periodic(self, interval_seconds: float) -> None:
        """Run once at startup, then continue at the explicit interval."""
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while True:
            try:
                await self.run_cycle()
            except ServiceError as exc:
                self._log_cycle_failure(self._gateway_exception_code(exc))
            except PlatformHttpError as exc:
                self._log_cycle_failure(self._gateway_exception_code(exc))
            except Exception as exc:
                self._log_cycle_failure(self._gateway_exception_code(exc))
            await asyncio.sleep(interval_seconds)

    @staticmethod
    def _log_cycle_failure(error_code: str) -> None:
        FinancialEffectRecovery._log_failure(
            error_code,
            stage="cycle",
            effect_operation_id=None,
        )

    @staticmethod
    def _log_failure(
        error_code: str,
        *,
        stage: str,
        effect_operation_id: str | None,
    ) -> None:
        logging.getLogger(__name__).warning(
            "Financial-effect recovery remains unresolved",
            extra={
                "error_code": error_code,
                "recovery_stage": stage,
                "effect_operation_id": effect_operation_id,
            },
        )


__all__ = ["FinancialEffectRecovery"]

"""Gateway failures after a Bank acknowledgment retain their actual origin."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.financial_effect_recovery import FinancialEffectRecovery
from tests.fakes.board_mp08_recovery import (
    EFFECT_OPERATION_ID,
    RECOVERY_WORKER_ID,
    RecoveryBank,
    RecoveryStore,
    diagnostic_envelope,
    empty_lease_envelope,
    lease_envelope,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("failure", ["connect", "timeout", "conflict"])
async def test_postdispatch_gateway_failure_keeps_origin_and_conflict_semantics(
    failure: str,
) -> None:
    class PostDispatchFailure(RecoveryStore):
        def finalize_task_effect_delivery(self, effect_operation_id: str) -> dict[str, Any]:
            if not self.native_calls:
                return super().finalize_task_effect_delivery(effect_operation_id)
            self.native_calls.append(effect_operation_id)
            if failure == "connect":
                raise httpx.ConnectError("private Gateway transport detail")
            if failure == "timeout":
                raise httpx.ReadTimeout("private Gateway timeout detail")
            raise ServiceError("effect_evidence_conflict", "conflict", 409, {})

    store = PostDispatchFailure(
        leases=[lease_envelope(), empty_lease_envelope()],
        finalize_native=[diagnostic_envelope(state="leased")],
        failures=[diagnostic_envelope(state="pending")],
    )
    bank = RecoveryBank(release_outcomes=[{"untrusted_ack": True}])
    worker = FinancialEffectRecovery(
        store=store,
        bank=bank,
        worker_id=RECOVERY_WORKER_ID,
        max_delivery_attempts_per_cycle=10,
    )
    assert await worker.run_cycle() == 1
    assert len(bank.release_calls) == 1
    assert store.native_calls == [EFFECT_OPERATION_ID, EFFECT_OPERATION_ID]
    if failure == "conflict":
        assert store.failure_calls == []
    else:
        expected = "gateway_unavailable" if failure == "connect" else "request_timeout"
        assert store.failure_calls == [(EFFECT_OPERATION_ID, 3, RECOVERY_WORKER_ID, expected)]

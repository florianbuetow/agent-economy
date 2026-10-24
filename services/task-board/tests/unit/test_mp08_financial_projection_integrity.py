"""Reject contradictory public claims about receipt-backed payment and recovery."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from task_board_service.task_contract_projection import FinancialEffect

pytestmark = pytest.mark.unit
_OPERATION = "op-11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("receipt_state", ["absent", "confirmed"])
def test_conflicting_business_projection_cannot_claim_an_automatic_pending_update(
    receipt_state: str,
) -> None:
    with pytest.raises(ValidationError):
        FinancialEffect.model_validate(
            {
                "schema_version": 1,
                "settlement_state": "conflict",
                "receipt_state": receipt_state,
                "outcome": "worker_paid" if receipt_state == "confirmed" else None,
                "operation_id": _OPERATION,
                "receipt_reference": _OPERATION if receipt_state == "confirmed" else None,
                "business_projection_pending": True,
                "pending_target_status": "approved",
            }
        )


@pytest.mark.parametrize(
    ("outcome", "target"),
    [("worker_paid", "cancelled"), ("poster_refunded", "approved"), ("split", "expired")],
)
def test_confirmed_money_cannot_be_paired_with_a_different_business_outcome(
    outcome: str,
    target: str,
) -> None:
    with pytest.raises(ValidationError):
        FinancialEffect.model_validate(
            {
                "schema_version": 1,
                "settlement_state": "pending",
                "receipt_state": "confirmed",
                "outcome": outcome,
                "operation_id": _OPERATION,
                "receipt_reference": _OPERATION,
                "business_projection_pending": True,
                "pending_target_status": target,
            }
        )

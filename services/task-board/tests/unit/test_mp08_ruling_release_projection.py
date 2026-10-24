"""Court endpoint awards use release receipts while awaiting the ruled projection."""

import pytest

from task_board_service.task_contract_projection import FinancialEffect

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("outcome", ["worker_paid", "poster_refunded"])
def test_full_court_award_can_await_the_ruled_projection(outcome: str) -> None:
    operation_id = "op-11111111-1111-4111-8111-111111111111"
    effect = {
        "schema_version": 1,
        "settlement_state": "pending",
        "receipt_state": "confirmed",
        "outcome": outcome,
        "operation_id": operation_id,
        "receipt_reference": operation_id,
        "business_projection_pending": True,
        "pending_target_status": "ruled",
    }
    assert FinancialEffect.model_validate(effect).model_dump() == effect

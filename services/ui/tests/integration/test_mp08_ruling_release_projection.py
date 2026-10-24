"""Public UI truth preserves full Court awards before the task update completes."""

from typing import Any

import pytest
from test_mp08_authoritative_task_reads import _detail, _Reader

from ui_service.core.state import get_app_state

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("outcome", ["worker_paid", "poster_refunded"])
async def test_full_court_award_survives_the_public_ui_projection(
    client: Any,
    outcome: str,
) -> None:
    task = _detail(status="disputed", accepted_bid_id="bid-accepted")
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
    task["financial_effect"] = effect
    get_app_state().task_board_reader = _Reader(detail=task)
    response = await client.get("/api/tasks/t-task1")
    assert response.status_code == 200, response.text
    assert response.json()["financial_effect"] == effect

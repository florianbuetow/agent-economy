"""The public duplicate-bid code remains distinct from Gateway vocabulary."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.task_bidding import TaskBiddingCoordinator

if TYPE_CHECKING:
    from task_board_service.services.task_manager import TaskManager

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("internal_code", "status", "public_code"),
    [
        ("bid_exists", 409, "bid_already_exists"),
        ("bid_exists", 503, "bid_exists"),
        ("operation_id_conflict", 409, "operation_id_conflict"),
        ("task_version_conflict", 409, "task_version_conflict"),
        ("cutoff_reached", 409, "cutoff_reached"),
        ("forbidden", 403, "forbidden"),
    ],
)
async def test_bid_claim_error_keeps_the_public_contract(
    internal_code: str, status: int, public_code: str
) -> None:
    task_id = "t-00000000-0000-4000-8000-000000000123"
    manager = MagicMock()
    manager.token_validator.validate_jws_token = AsyncMock(
        return_value={
            "_signer_id": "a-worker",
            "task_id": task_id,
            "bidder_id": "a-worker",
            "amount": 10,
            "operation_id": "op-00000000-0000-4000-8000-000000000123",
            "expected_lifecycle_version": 0,
        }
    )
    failure = ServiceError(internal_code, "Authoritative claim rejected", status, {})
    manager.store.claim_task_operation.side_effect = failure
    coordinator = TaskBiddingCoordinator(cast("TaskManager", manager))
    with pytest.raises(ServiceError) as raised:
        await coordinator.submit_bid(task_id, "signed-token")
    assert raised.value.error == public_code
    assert raised.value.status_code == status
    assert raised.value.message == failure.message
    assert raised.value.details == failure.details
    manager.store.claim_task_operation.assert_called_once()
    manager.store.get_bid.assert_not_called()
    if public_code == internal_code:
        assert raised.value is failure

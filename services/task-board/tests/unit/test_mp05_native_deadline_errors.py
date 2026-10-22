"""Native acceptance-window validation preserves the public deadline error contract."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, Mock

import pytest
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingResponse
from service_commons.exceptions import ServiceError

from task_board_service.services.task_creation import TaskCreationCoordinator

if TYPE_CHECKING:
    from task_board_service.services.task_manager import TaskManager

pytestmark = pytest.mark.unit


def _payload() -> dict[str, object]:
    task_id = "t-00000000-0000-4000-8000-000000000001"
    poster_id = "a-poster"
    return {
        "action": "create_task",
        "protocol_version": 1,
        "operation_id": "op-00000000-0000-4000-8000-000000000002",
        "task_id": task_id,
        "poster_id": poster_id,
        "_signer_id": poster_id,
        "lock_operation_id": operation_id_for("task-lock", (task_id, poster_id)),
        "title": "Deadline vocabulary",
        "spec": "Reject invalid task windows before effects.",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 1,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 60,
    }


@pytest.mark.parametrize("window", [None, True, 1.5, "1", 0, -1, 9_223_372_036_855])
async def test_invalid_native_acceptance_window_is_deadline_error_before_effects(
    window: object,
) -> None:
    payload = _payload()
    payload["acceptance_window_seconds"] = window
    tokens = SimpleNamespace(
        validate_jws_token=AsyncMock(return_value=payload),
        validate_escrow_jws_token=AsyncMock(
            side_effect=AssertionError("invalid window reached escrow")
        ),
    )
    bank = AsyncMock()
    manager = SimpleNamespace(token_validator=tokens, central_bank_client=bank)
    coordinator = TaskCreationCoordinator(cast("TaskManager", manager))
    with pytest.raises(ServiceError) as error:
        await coordinator.create_task("task-token", "escrow-token")
    assert (error.value.status_code, error.value.error) == (400, "invalid_deadline")
    assert "acceptance_window_seconds" in error.value.message
    tokens.validate_escrow_jws_token.assert_not_called()
    assert bank.mock_calls == []


async def test_minimum_explicit_acceptance_window_remains_valid() -> None:
    payload = _payload()
    tokens = SimpleNamespace(
        validate_jws_token=AsyncMock(return_value=payload),
        validate_escrow_jws_token=AsyncMock(
            return_value={
                "action": "escrow_lock",
                "operation_id": payload["lock_operation_id"],
                "task_id": payload["task_id"],
                "agent_id": payload["poster_id"],
                "amount": payload["reward"],
                "_signer_id": payload["poster_id"],
            }
        ),
    )
    published = TaskPostingResponse(201, {"publication_state": "published"})
    store = SimpleNamespace(admit_task_publication=Mock(return_value=published))
    bank = AsyncMock()
    manager = SimpleNamespace(token_validator=tokens, store=store, central_bank_client=bank)
    result = await TaskCreationCoordinator(cast("TaskManager", manager)).create_task(
        "task-token", "escrow-token"
    )
    assert result == published
    assert store.admit_task_publication.call_args.args[0].acceptance_window_seconds == 1
    assert bank.mock_calls == []

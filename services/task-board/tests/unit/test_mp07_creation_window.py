"""MP-07 creation-boundary proofs for explicit acceptance windows."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from service_auth.financial_identity import operation_id_for
from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse
from service_commons.exceptions import ServiceError

from task_board_service.services.task_creation import TaskCreationCoordinator
from task_board_service.task_operation_contract import MAX_INTEGER

if TYPE_CHECKING:
    from collections.abc import Iterator

    from task_board_service.services.task_manager import TaskManager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_MISSING = object()


class _Store:
    """Coordinator boundary double; real receipt proof lives in Gateway tests."""

    def __init__(self) -> None:
        self.admitted: list[TaskPostingIntent] = []
        self.reconciled: list[str] = []

    def admit_task_publication(self, intent: TaskPostingIntent) -> TaskPostingResponse:
        self.admitted.append(intent)
        return TaskPostingResponse(202, {"publication_state": "awaiting_lock"})

    def reconcile_task_publication(
        self, operation_id: str, intent: TaskPostingIntent | None
    ) -> TaskPostingResponse:
        assert intent is not None and self.admitted == [intent]
        assert operation_id == intent.operation_id
        self.reconciled.append(operation_id)
        if len(self.reconciled) == 1:
            return TaskPostingResponse(202, {"publication_state": "awaiting_lock"})
        return TaskPostingResponse(
            201,
            {
                "publication_state": "published",
                "result": {
                    "task_id": intent.task_id,
                    "acceptance_window_seconds": intent.acceptance_window_seconds,
                },
            },
        )


@pytest.fixture
def creation_boundary() -> Iterator[
    tuple[TaskCreationCoordinator, SimpleNamespace, _Store, dict[str, Any]]
]:
    poster_id = f"a-poster-{uuid4()}"
    task_id = f"t-{uuid4()}"
    reward = 100
    operation_id = operation_id_for("task-lock", (task_id, poster_id))
    payload: dict[str, Any] = {
        "action": "create_task",
        "protocol_version": 1,
        "operation_id": f"op-{uuid4()}",
        "lock_operation_id": operation_id,
        "_signer_id": poster_id,
        "task_id": task_id,
        "poster_id": poster_id,
        "title": "Explicit acceptance window",
        "spec": "Persist the poster-selected window without a default.",
        "reward": reward,
        "bidding_deadline_seconds": 60,
        "execution_deadline_seconds": 120,
        "review_deadline_seconds": 90,
    }
    token_validator = SimpleNamespace(
        validate_jws_token=AsyncMock(return_value=payload),
        validate_escrow_jws_token=AsyncMock(
            return_value={
                "action": "escrow_lock",
                "task_id": task_id,
                "amount": reward,
                "operation_id": operation_id,
                "agent_id": poster_id,
                "_signer_id": poster_id,
            }
        ),
    )
    central_bank_client = SimpleNamespace(escrow_lock=AsyncMock(return_value=None))
    store = _Store()
    manager = SimpleNamespace(
        token_validator=token_validator,
        central_bank_client=central_bank_client,
        store=store,
        escrow_coordinator=SimpleNamespace(release_posting_compensation=AsyncMock()),
        logger=Mock(),
    )
    yield TaskCreationCoordinator(cast("TaskManager", manager)), manager, store, payload


@pytest.mark.parametrize(
    ("window", "expected_error"),
    [
        (_MISSING, "invalid_payload"),
        (None, "invalid_deadline"),
        (0, "invalid_deadline"),
        (-1, "invalid_deadline"),
        (True, "invalid_deadline"),
        (1.5, "invalid_deadline"),
        ("45", "invalid_deadline"),
        (MAX_INTEGER // 1_000_000 + 1, "invalid_deadline"),
    ],
    ids=["missing", "null", "zero", "negative", "bool", "float", "string", "overflow"],
)
async def test_invalid_acceptance_window_fails_before_bank_or_store(
    creation_boundary: tuple[TaskCreationCoordinator, SimpleNamespace, _Store, dict[str, Any]],
    window: object,
    expected_error: str,
) -> None:
    coordinator, manager, store, payload = creation_boundary
    if window is not _MISSING:
        payload["acceptance_window_seconds"] = window

    with pytest.raises(ServiceError) as raised:
        await coordinator.create_task("task-token", "escrow-token")

    assert raised.value.status_code == 400
    assert raised.value.error == expected_error
    assert (
        "required fields" if window is _MISSING else "acceptance_window_seconds"
    ) in raised.value.message
    manager.token_validator.validate_escrow_jws_token.assert_not_awaited()
    assert manager.central_bank_client.escrow_lock.await_count == 0
    assert store.admitted == []
    assert store.reconciled == []


async def test_explicit_acceptance_window_reaches_persisted_task_input(
    creation_boundary: tuple[TaskCreationCoordinator, SimpleNamespace, _Store, dict[str, Any]],
) -> None:
    coordinator, manager, store, payload = creation_boundary
    payload["acceptance_window_seconds"] = 45

    response = await coordinator.create_task("task-token", "escrow-token")

    assert manager.central_bank_client.escrow_lock.await_count == 1
    assert len(store.admitted) == 1
    assert store.admitted[0].acceptance_window_seconds == 45
    assert store.reconciled == [payload["operation_id"], payload["operation_id"]]
    assert response.status_code == 201
    assert response.body["publication_state"] == "published"
    assert response.body["result"]["task_id"] == payload["task_id"]
    assert response.body["result"]["acceptance_window_seconds"] == 45

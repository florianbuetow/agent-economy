"""MP-07 creation-boundary proofs for explicit acceptance windows."""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.services.task_creation import TaskCreationCoordinator
from task_board_service.task_operation_contract import MAX_INTEGER

if TYPE_CHECKING:
    from collections.abc import Iterator

    from task_board_service.services.task_manager import TaskManager

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_MISSING = object()
_NOW = "2099-01-01T00:00:00Z"


def _parsed_token(_token: str, poster_id: str) -> SimpleNamespace:
    return SimpleNamespace(header=SimpleNamespace(kid=poster_id))


class _Store:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        stored = next((dict(task) for task in self.inserted if task["task_id"] == task_id), None)
        if stored is not None:
            stored["dispute_id"] = None
            stored.update(
                {
                    "lifecycle_version": 0,
                    "timing_contract_version": 2,
                    "bidding_cutoff_us": 1_000_000,
                    "acceptance_cutoff_us": 1_000_000
                    + int(stored["acceptance_window_seconds"]) * 1_000_000,
                    "execution_cutoff_us": None,
                    "review_cutoff_us": None,
                    "accepted_reward": None,
                    "accepted_bid_amount": None,
                    "pending_transition": None,
                    "legacy_reconciliations": [],
                }
            )
        return stored

    def insert_task(self, task: dict[str, Any]) -> None:
        self.inserted.append(dict(task))


@pytest.fixture
def creation_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TaskCreationCoordinator, SimpleNamespace, _Store, dict[str, Any]]]:
    poster_id = f"a-poster-{uuid4()}"
    task_id = f"t-{uuid4()}"
    reward = 100
    operation_id = operation_id_for("task-lock", (task_id, poster_id))
    payload: dict[str, Any] = {
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
        decode_escrow_token_payload=Mock(
            return_value={
                "task_id": task_id,
                "amount": reward,
                "operation_id": operation_id,
                "agent_id": poster_id,
            }
        ),
    )
    escrow_id = f"esc-{uuid4()}"
    central_bank_client = SimpleNamespace(
        escrow_lock=AsyncMock(
            return_value={
                "outcome": "native_receipt",
                "receipt": {
                    "receipt_version": 1,
                    "operation_id": operation_id,
                    "operation_kind": "escrow_lock",
                    "source_reference": task_id,
                    "escrow_id": escrow_id,
                    "result": {"escrow_status_at_commit": "locked"},
                    "legs": [
                        {
                            "role": "payer",
                            "account_id": poster_id,
                            "direction": "debit",
                            "amount": reward,
                        }
                    ],
                },
                "current_escrow": {
                    "escrow_id": escrow_id,
                    "payer_account_id": poster_id,
                    "task_id": task_id,
                    "amount": reward,
                    "status": "locked",
                    "resolved_at": None,
                },
            }
        )
    )
    store = _Store()
    manager = SimpleNamespace(
        token_validator=token_validator,
        central_bank_client=central_bank_client,
        store=store,
        escrow_coordinator=SimpleNamespace(release_posting_compensation=AsyncMock()),
        logger=Mock(),
    )
    monkeypatch.setattr(
        "task_board_service.services.task_creation.parse_jws",
        partial(_parsed_token, poster_id=poster_id),
    )
    monkeypatch.setattr("task_board_service.services.task_creation.now_iso", lambda: _NOW)
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
    assert "acceptance_window_seconds" in raised.value.message
    assert manager.token_validator.decode_escrow_token_payload.call_count == 0
    assert manager.central_bank_client.escrow_lock.await_count == 0
    assert store.inserted == []


async def test_explicit_acceptance_window_reaches_persisted_task_input(
    creation_boundary: tuple[TaskCreationCoordinator, SimpleNamespace, _Store, dict[str, Any]],
) -> None:
    coordinator, manager, store, payload = creation_boundary
    payload["acceptance_window_seconds"] = 45

    response = await coordinator.create_task("task-token", "escrow-token")

    assert manager.central_bank_client.escrow_lock.await_count == 1
    assert len(store.inserted) == 1
    assert store.inserted[0]["acceptance_window_seconds"] == 45
    assert response["task_id"] == payload["task_id"]
    assert response["acceptance_window_seconds"] == 45

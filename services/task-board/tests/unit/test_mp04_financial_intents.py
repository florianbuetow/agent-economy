"""MP-04 Task Board financial-intent and receipt acceptance tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.task_creation import new_lock_escrow_id
from tests.fakes.in_memory_task_store import InMemoryTaskStore


def _lock_envelope(
    *,
    task_id: str,
    poster_id: str,
    amount: int,
    status: str,
) -> dict[str, Any]:
    operation_id = operation_id_for("task-lock", (task_id, poster_id))
    escrow_id = "esc-native-lock"
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": "escrow_lock",
            "request_sha256": "a" * 64,
            "source_reference": task_id,
            "committed_at": "2026-10-16T00:00:00Z",
            "event_id": 7,
            "escrow_id": escrow_id,
            "result": {"escrow_status_at_commit": "locked"},
            "legs": [
                {
                    "role": "payer",
                    "account_id": poster_id,
                    "direction": "debit",
                    "amount": amount,
                    "transaction_id": "tx-lock",
                    "balance_after": 900,
                }
            ],
        },
        "current_escrow": {
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "task_id": task_id,
            "amount": amount,
            "status": status,
            "created_at": "2026-10-16T00:00:00Z",
            "resolved_at": None,
        },
    }


@pytest.mark.unit
def test_new_task_accepts_only_matching_actually_locked_native_receipt() -> None:
    """A new task is funded only by the matching native lock receipt."""
    task_id = "t-00000000-0000-4000-8000-000000000001"
    poster_id = "a-poster"
    operation_id = operation_id_for("task-lock", (task_id, poster_id))
    envelope = _lock_envelope(
        task_id=task_id,
        poster_id=poster_id,
        amount=100,
        status="locked",
    )

    assert (
        new_lock_escrow_id(
            envelope,
            operation_id=operation_id,
            task_id=task_id,
            poster_id=poster_id,
            reward=100,
        )
        == "esc-native-lock"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutation",
    [
        "legacy",
        "settled",
        "wrong_task",
        "wrong_payer",
        "wrong_amount",
        "wrong_operation",
    ],
)
def test_new_task_rejects_non_proof_lock_responses(mutation: str) -> None:
    """Legacy, settled, or mismatched observations cannot fund a new task."""
    task_id = "t-00000000-0000-4000-8000-000000000001"
    poster_id = "a-poster"
    operation_id = operation_id_for("task-lock", (task_id, poster_id))
    envelope = _lock_envelope(
        task_id=task_id,
        poster_id=poster_id,
        amount=100,
        status="locked",
    )
    if mutation == "legacy":
        envelope = {"outcome": "legacy_observation", "escrow_id": "esc-native-lock"}
    elif mutation == "settled":
        envelope["current_escrow"]["status"] = "released"
    elif mutation == "wrong_task":
        envelope["current_escrow"]["task_id"] = "t-other"
    elif mutation == "wrong_payer":
        envelope["receipt"]["legs"][0]["account_id"] = "a-other"
    elif mutation == "wrong_amount":
        envelope["current_escrow"]["amount"] = 99
    else:
        envelope["receipt"]["operation_id"] = operation_id_for("task-lock", (task_id, "a-other"))

    with pytest.raises(ServiceError) as exc_info:
        new_lock_escrow_id(
            envelope,
            operation_id=operation_id,
            task_id=task_id,
            poster_id=poster_id,
            reward=100,
        )

    assert exc_info.value.error == "central_bank_unavailable"
    assert exc_info.value.status_code == 502


@pytest.mark.unit
async def test_approval_and_pending_retry_reuse_one_financial_intent(tmp_path: Path) -> None:
    """An approval retry cannot allocate a second financial operation identity."""
    store = InMemoryTaskStore(db_path=str(tmp_path / "task-board.db"))
    bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=bank, store=store)
    expected_operation_id = operation_id_for("task-settlement", ("t-1", "approved"))

    await coordinator.release_task_escrow("t-1", "approved", "esc-1", "a-worker")
    await coordinator.release_task_escrow("t-1", "approved", "esc-1", "a-worker")

    expected_call = {
        "escrow_id": "esc-1",
        "recipient_account_id": "a-worker",
        "operation_id": expected_operation_id,
        "source_reference": "task:t-1:approved",
    }
    assert bank.escrow_release.await_count == 2
    assert bank.escrow_release.await_args_list[0].kwargs == expected_call
    assert bank.escrow_release.await_args_list[1].kwargs == expected_call
    store.close()


@pytest.mark.unit
async def test_opposing_task_settlements_have_distinct_financial_intents(tmp_path: Path) -> None:
    """Approval, expiry, cancellation, and compensation cannot alias each other."""
    store = InMemoryTaskStore(db_path=str(tmp_path / "task-board.db"))
    bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=bank, store=store)

    for action in ("approved", "expired", "cancelled", "posting_compensation"):
        await coordinator.release_task_escrow("t-1", action, "esc-1", "a-recipient")

    operation_ids = [call.kwargs["operation_id"] for call in bank.escrow_release.await_args_list]
    source_references = [
        call.kwargs["source_reference"] for call in bank.escrow_release.await_args_list
    ]
    assert len(set(operation_ids)) == 4
    assert source_references == [
        "task:t-1:approved",
        "task:t-1:expired",
        "task:t-1:cancelled",
        "task:t-1:posting_compensation",
    ]
    store.close()


@pytest.mark.unit
async def test_ruling_release_and_split_share_actual_ruling_identity(tmp_path: Path) -> None:
    """All payout percentages use the same actual ruling-owned intent."""
    store = InMemoryTaskStore(db_path=str(tmp_path / "task-board.db"))
    bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=bank, store=store)
    expected_operation_id = operation_id_for("task-settlement", ("t-1", "ruling", "ruling-9"))

    await coordinator.release_ruling_escrow("t-1", "ruling-9", "esc-1", "a-poster")
    await coordinator.split_ruling_escrow("t-1", "ruling-9", "esc-1", "a-worker", "a-poster", 50)

    assert bank.escrow_release.await_args.kwargs["operation_id"] == expected_operation_id
    assert bank.escrow_split.await_args.kwargs["operation_id"] == expected_operation_id
    assert bank.escrow_release.await_args.kwargs["source_reference"] == ("task:t-1:ruling:ruling-9")
    assert bank.escrow_split.await_args.kwargs["source_reference"] == ("task:t-1:ruling:ruling-9")
    store.close()

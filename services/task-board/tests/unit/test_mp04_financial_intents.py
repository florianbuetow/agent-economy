"""MP-04 Task Board financial-intent and receipt acceptance tests."""

from __future__ import annotations

import hashlib
import json
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.services.task_creation import new_lock_escrow_id
from task_board_service.services.task_operations import complete_bank_operation
from tests.fakes.native_task_fixture import native_task_context
from tests.unit.test_escrow_coordinator import (
    TERMS,
    claim_approval,
    claim_expiry,
    prepare_submitted,
)
from tests.unit.test_mp04_ruling_replay import persist_native_split

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.fakes.native_task_fixture import NativeTaskFixture


def _lock_envelope(
    *,
    task_id: str,
    poster_id: str,
    amount: int,
    status: str,
) -> dict[str, Any]:
    operation_id = operation_id_for("task-lock", (task_id, poster_id))
    escrow_id = "esc-native-lock"
    canonical_request = {
        "request_version": 1,
        "operation_kind": "escrow_lock",
        "payer_account_id": poster_id,
        "task_id": task_id,
        "amount": amount,
    }
    return {
        "outcome": "native_receipt",
        "receipt": {
            "receipt_version": 1,
            "operation_id": operation_id,
            "operation_kind": "escrow_lock",
            "request_sha256": hashlib.sha256(
                json.dumps(
                    canonical_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest(),
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
        envelope["current_escrow"]["resolved_at"] = "2026-10-16T00:01:00Z"
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


@pytest.fixture(name="native_tasks")
def native_tasks_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[NativeTaskFixture]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        yield scenario


@pytest.mark.unit
async def test_approval_and_pending_retry_reuse_one_financial_intent(
    native_tasks: NativeTaskFixture,
) -> None:
    prepare_submitted(native_tasks, "t-1")
    operation_id = "op-00000000-0000-4000-8000-000000000901"
    claimed = claim_approval(native_tasks, "t-1", operation_id)
    native_tasks.bank.escrow_release.side_effect = ServiceError(
        "central_bank_unavailable", "injected", 502, {}
    )
    with pytest.raises(ServiceError):
        await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
    assert native_tasks.task("t-1")["status"] == "submitted"
    assert native_tasks.task("t-1")["pending_transition"]["operation_id"] == operation_id
    native_tasks.bank.escrow_release.side_effect = native_tasks.persist_release
    await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
    expected = {
        "escrow_id": "esc-t-1",
        "recipient_account_id": native_tasks.worker_id,
        "operation_id": operation_id,
        "source_reference": "task:t-1:approved",
    }
    assert native_tasks.bank.escrow_release.await_count == 2
    assert native_tasks.bank.escrow_release.await_args_list[0].kwargs == expected
    assert native_tasks.bank.escrow_release.await_args_list[1].kwargs == expected
    native_tasks.assert_settlement(
        "t-1",
        command="approve_task",
        status="approved",
        recipient=native_tasks.worker_id,
        event_type="task.approved",
    )


@pytest.mark.unit
async def test_opposing_task_settlements_have_distinct_financial_intents(
    native_tasks: NativeTaskFixture,
) -> None:
    prepare_submitted(native_tasks, "t-approve")
    native_tasks.open_task("t-cancel", TERMS)
    native_tasks.open_task("t-expire", TERMS)
    approval_id = "op-00000000-0000-4000-8000-000000000901"
    cancel_id = "op-00000000-0000-4000-8000-000000000902"
    expiry_id = "op-00000000-0000-4000-8000-000000000903"
    claimed = claim_approval(native_tasks, "t-approve", approval_id)
    await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
    task = native_tasks.task("t-cancel")
    cancel = native_tasks.command(
        "t-cancel",
        "cancel_task",
        "open",
        0,
        native_tasks.poster_id,
        {
            "poster_id": native_tasks.poster_id,
            "escrow_id": task["escrow_id"],
            "reward": 100,
            "source_reference": "task:t-cancel:cancelled",
            "cutoff_us": task["bidding_cutoff_us"],
        },
    )
    # Identity reuse across different native intents must conflict, not mint another effect ID.
    cancel["operation_id"] = approval_id
    with pytest.raises(ServiceError) as conflict:
        native_tasks.store.claim_task_operation(cancel)
    assert (conflict.value.status_code, conflict.value.error) == (409, "operation_id_conflict")
    assert native_tasks.task("t-cancel") == task
    assert native_tasks.bank.escrow_release.await_count == 1
    cancel["operation_id"] = cancel_id
    cancel_claim = native_tasks.store.claim_task_operation(cancel)
    await complete_bank_operation(native_tasks.store, native_tasks.escrow, cancel_claim)

    # Actual locked funds without a published Board row model failed publication.
    native_tasks.store.writer.escrow_lock(
        {
            "operation_id": operation_id_for("task-lock", ("t-posting", native_tasks.poster_id)),
            "escrow_id": "esc-posting",
            "payer_account_id": native_tasks.poster_id,
            "amount": 100,
            "task_id": "t-posting",
            "created_at": native_tasks.clock.iso(),
            "tx_id": "tx-lock-posting",
            "event": native_tasks.event("bank", "escrow.locked", "t-posting"),
        }
    )
    assert native_tasks.store.get_task("t-posting") is None
    await native_tasks.escrow.release_posting_compensation(
        "t-posting", "esc-posting", native_tasks.poster_id
    )
    await native_tasks.escrow.release_posting_compensation(
        "t-posting", "esc-posting", native_tasks.poster_id
    )
    compensation_id = operation_id_for("task-settlement", ("t-posting", "posting_compensation"))
    assert (
        native_tasks.store.writer.connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?", (compensation_id,)
        ).fetchone()[0]
        == 1
    )

    native_tasks.clock.at_seconds(3600)
    expiry_claim = claim_expiry(native_tasks, "t-expire", expiry_id)
    await complete_bank_operation(native_tasks.store, native_tasks.escrow, expiry_claim)
    calls = native_tasks.bank.escrow_release.await_args_list
    assert [call.kwargs["operation_id"] for call in calls] == [
        approval_id,
        cancel_id,
        compensation_id,
        compensation_id,
        expiry_id,
    ]
    assert len({call.kwargs["operation_id"] for call in calls}) == 4
    assert [call.kwargs["source_reference"] for call in calls] == [
        "task:t-approve:approved",
        "task:t-cancel:cancelled",
        "task:t-posting:posting_compensation",
        "task:t-posting:posting_compensation",
        "task:t-expire:expired",
    ]
    native_tasks.assert_settlement(
        "t-cancel",
        command="cancel_task",
        status="cancelled",
        recipient=native_tasks.poster_id,
        event_type="task.cancelled",
    )
    native_tasks.assert_settlement(
        "t-expire",
        command="expire_bidding",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )


@pytest.mark.unit
@pytest.mark.parametrize("worker_pct", [0, 50, 100])
async def test_ruling_release_and_split_retain_the_claimed_operation_identity(
    native_tasks: NativeTaskFixture,
    worker_pct: int,
) -> None:
    prepare_submitted(native_tasks, "t-ruling")
    native_tasks.dispute("t-ruling", rebuttal_seconds=3600)
    native_tasks.bank.escrow_split = AsyncMock(
        side_effect=partial(persist_native_split, native_tasks)
    )
    operation_id = "op-00000000-0000-4000-8000-000000000904"
    arguments = native_tasks.accepted_arguments("t-ruling")
    arguments.update(
        {
            "ruling_id": "clm-t-ruling",
            "worker_pct": worker_pct,
            "summary": "Explicit frozen ruling",
            "source_reference": "task:t-ruling:ruling:clm-t-ruling",
        }
    )
    request = native_tasks.command(
        "t-ruling", "record_ruling", "disputed", 3, "a-platform", arguments
    )
    request.update({"actor_kind": "platform", "operation_id": operation_id})
    claimed = native_tasks.store.claim_task_operation(request)
    assert claimed["effect_operation_id"] == operation_id
    completed = await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
    assert completed["operation_state"] == "completed"
    delivery = (
        native_tasks.bank.escrow_split if worker_pct == 50 else native_tasks.bank.escrow_release
    )
    delivery.assert_awaited_once()
    assert delivery.await_args.kwargs["operation_id"] == operation_id
    assert delivery.await_args.kwargs["source_reference"] == "task:t-ruling:ruling:clm-t-ruling"
    assert native_tasks.task("t-ruling")["status"] == "ruled"
    paid = native_tasks.store.writer.connection.execute(
        "SELECT account_id,amount FROM bank_operation_legs "
        "WHERE operation_id=? ORDER BY account_id",
        (operation_id,),
    ).fetchall()
    expected = (
        [(native_tasks.poster_id, 100)]
        if worker_pct == 0
        else [(native_tasks.worker_id, 100)]
        if worker_pct == 100
        else sorted([(native_tasks.poster_id, 50), (native_tasks.worker_id, 50)])
    )
    assert [tuple(row) for row in paid] == expected
    changed = {**request, "arguments": {**arguments, "worker_pct": (worker_pct + 50) % 150}}
    with pytest.raises(ServiceError) as conflict:
        native_tasks.store.claim_task_operation(changed)
    assert (conflict.value.status_code, conflict.value.error) == (409, "operation_id_conflict")
    assert (
        native_tasks.bank.escrow_release.await_count + native_tasks.bank.escrow_split.await_count
        == 1
    )

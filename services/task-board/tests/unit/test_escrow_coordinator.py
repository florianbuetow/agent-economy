"""Bank transport and native ownership replace legacy status-derived escrow retries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.task_operations import complete_bank_operation
from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from service_clients.bank import BankClient

    from tests.fakes.native_task_fixture import NativeTaskFixture

_APPROVAL_OPERATION_ID = "op-00000000-0000-4000-8000-000000000001"
_APPROVAL_SOURCE_REFERENCE = "task:t-1:approved"
_SPLIT_OPERATION_ID = "op-00000000-0000-4000-8000-000000000002"
_SPLIT_SOURCE_REFERENCE = "task:t-1:ruling:clm-t-1"
TERMS = TaskTerms(100, 3600, 600, 3600, 3600)


@pytest.fixture(name="native_tasks")
def native_tasks_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[NativeTaskFixture]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        yield scenario


def prepare_submitted(scenario: NativeTaskFixture, task_id: str) -> None:
    scenario.open_task(task_id, TERMS)
    scenario.bid(task_id, 90)
    scenario.accept(task_id, 90)
    scenario.submit(task_id)


def claim_approval(scenario: NativeTaskFixture, task_id: str, operation_id: str) -> dict[str, Any]:
    arguments = scenario.accepted_arguments(task_id)
    arguments["source_reference"] = f"task:{task_id}:approved"
    payload = scenario.command(
        task_id, "approve_task", "submitted", 2, scenario.poster_id, arguments
    )
    payload["operation_id"] = operation_id
    return scenario.store.claim_task_operation(payload)


def claim_expiry(scenario: NativeTaskFixture, task_id: str, operation_id: str) -> dict[str, Any]:
    task = scenario.task(task_id)
    return scenario.store.claim_task_operation(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": task_id,
            "command": "expire_bidding",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "system",
            "actor_id": scenario.authority.deadline_actor_id,
            "arguments": {
                "poster_id": scenario.poster_id,
                "escrow_id": task["escrow_id"],
                "reward": task["reward"],
                "source_reference": f"task:{task_id}:expired",
                "cutoff_us": task["bidding_cutoff_us"],
            },
        }
    )


def assert_pending(
    scenario: NativeTaskFixture, task_id: str, operation_id: str, source: str
) -> None:
    task = scenario.task(task_id)
    assert task["status"] == source
    assert task["pending_transition"]["operation_id"] == operation_id
    assert (
        scenario.store.writer.connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts WHERE operation_id=?", (operation_id,)
        ).fetchone()[0]
        == 0
    )


@pytest.mark.unit
async def test_release_escrow_success() -> None:
    """release_escrow calls Central Bank release endpoint."""
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=cast("BankClient", mock_bank))

    await coordinator.release_escrow(
        "esc-1",
        "a-recipient",
        _APPROVAL_OPERATION_ID,
        _APPROVAL_SOURCE_REFERENCE,
    )

    mock_bank.escrow_release.assert_awaited_once_with(
        escrow_id="esc-1",
        recipient_account_id="a-recipient",
        operation_id=_APPROVAL_OPERATION_ID,
        source_reference=_APPROVAL_SOURCE_REFERENCE,
    )


@pytest.mark.unit
async def test_release_escrow_service_error_propagates() -> None:
    """release_escrow re-raises ServiceError unchanged."""
    expected = ServiceError("central_bank_unavailable", "fail", 502, {})
    mock_bank = AsyncMock()
    mock_bank.escrow_release = AsyncMock(side_effect=expected)
    coordinator = EscrowCoordinator(central_bank_client=cast("BankClient", mock_bank))

    with pytest.raises(ServiceError) as exc_info:
        await coordinator.release_escrow(
            "esc-1",
            "a-recipient",
            _APPROVAL_OPERATION_ID,
            _APPROVAL_SOURCE_REFERENCE,
        )

    assert exc_info.value is expected


@pytest.mark.unit
async def test_release_escrow_generic_error_wraps() -> None:
    """release_escrow wraps generic exceptions as central_bank_unavailable."""
    mock_bank = AsyncMock()
    mock_bank.escrow_release = AsyncMock(side_effect=RuntimeError("boom"))
    coordinator = EscrowCoordinator(central_bank_client=cast("BankClient", mock_bank))

    with pytest.raises(ServiceError) as exc_info:
        await coordinator.release_escrow(
            "esc-1",
            "a-recipient",
            _APPROVAL_OPERATION_ID,
            _APPROVAL_SOURCE_REFERENCE,
        )

    assert exc_info.value.error == "central_bank_unavailable"
    assert exc_info.value.status_code == 502


@pytest.mark.unit
async def test_split_escrow_success() -> None:
    """split_escrow calls Central Bank split endpoint."""
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=cast("BankClient", mock_bank))

    await coordinator.split_escrow(
        "esc-1",
        "a-worker",
        "a-poster",
        65,
        _SPLIT_OPERATION_ID,
        _SPLIT_SOURCE_REFERENCE,
    )

    mock_bank.escrow_split.assert_awaited_once_with(
        escrow_id="esc-1",
        worker_account_id="a-worker",
        poster_account_id="a-poster",
        worker_pct=65,
        operation_id=_SPLIT_OPERATION_ID,
        source_reference=_SPLIT_SOURCE_REFERENCE,
    )


@pytest.mark.unit
async def test_split_escrow_service_error_propagates() -> None:
    """split_escrow re-raises ServiceError unchanged."""
    expected = ServiceError("central_bank_unavailable", "fail", 502, {})
    mock_bank = AsyncMock()
    mock_bank.escrow_split = AsyncMock(side_effect=expected)
    coordinator = EscrowCoordinator(central_bank_client=cast("BankClient", mock_bank))

    with pytest.raises(ServiceError) as exc_info:
        await coordinator.split_escrow(
            "esc-1",
            "a-worker",
            "a-poster",
            65,
            _SPLIT_OPERATION_ID,
            _SPLIT_SOURCE_REFERENCE,
        )

    assert exc_info.value is expected


@pytest.mark.unit
async def test_split_escrow_generic_error_wraps() -> None:
    """split_escrow wraps generic exceptions as central_bank_unavailable."""
    mock_bank = AsyncMock()
    mock_bank.escrow_split = AsyncMock(side_effect=RuntimeError("boom"))
    coordinator = EscrowCoordinator(central_bank_client=cast("BankClient", mock_bank))

    with pytest.raises(ServiceError) as exc_info:
        await coordinator.split_escrow(
            "esc-1",
            "a-worker",
            "a-poster",
            65,
            _SPLIT_OPERATION_ID,
            _SPLIT_SOURCE_REFERENCE,
        )

    assert exc_info.value.error == "central_bank_unavailable"
    assert exc_info.value.status_code == 502


@pytest.mark.unit
async def test_claimed_expiry_completes_only_after_persisted_release(
    native_tasks: NativeTaskFixture,
) -> None:
    native_tasks.open_task("t-1", TERMS)
    native_tasks.clock.at_seconds(3600)
    claimed = claim_expiry(native_tasks, "t-1", _APPROVAL_OPERATION_ID)
    assert_pending(native_tasks, "t-1", _APPROVAL_OPERATION_ID, "open")
    result = await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
    assert result["operation_state"] == "completed"
    native_tasks.assert_settlement(
        "t-1",
        command="expire_bidding",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )


@pytest.mark.unit
async def test_failed_delivery_does_not_complete_expiry(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task("t-1", TERMS)
    native_tasks.clock.at_seconds(3600)
    claimed = claim_expiry(native_tasks, "t-1", _APPROVAL_OPERATION_ID)
    expected = ServiceError("central_bank_unavailable", "injected", 502, {})
    native_tasks.bank.escrow_release.side_effect = expected
    with pytest.raises(ServiceError) as caught:
        await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
    assert caught.value is expected
    assert_pending(native_tasks, "t-1", _APPROVAL_OPERATION_ID, "open")
    assert (
        native_tasks.store.writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id='t-1' AND event_type='task.expired'"
        ).fetchone()[0]
        == 0
    )


@pytest.mark.unit
async def test_unowned_open_state_has_no_implicit_release(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task("t-1", TERMS)
    task = native_tasks.task("t-1")
    result = await native_tasks.evaluator(None).evaluate_deadline(task)
    assert result == task
    # An inconsistent supplied flag still cannot create native recovery ownership.
    flagged = {**task, "escrow_pending": 1}
    assert (await native_tasks.evaluator(None).evaluate_deadline(flagged))["status"] == "open"
    assert native_tasks.task("t-1") == task
    native_tasks.bank.escrow_release.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["expiry", "approval"])
async def test_pending_retry_completes_the_same_owner(
    native_tasks: NativeTaskFixture, kind: str
) -> None:
    if kind == "expiry":
        native_tasks.open_task("t-1", TERMS)
        native_tasks.clock.at_seconds(3600)
        claimed = claim_expiry(native_tasks, "t-1", _APPROVAL_OPERATION_ID)
        source, target, recipient, command, event = (
            "open",
            "expired",
            native_tasks.poster_id,
            "expire_bidding",
            "task.expired",
        )
    else:
        prepare_submitted(native_tasks, "t-1")
        claimed = claim_approval(native_tasks, "t-1", _APPROVAL_OPERATION_ID)
        source, target, recipient, command, event = (
            "submitted",
            "approved",
            native_tasks.worker_id,
            "approve_task",
            "task.approved",
        )
    native_tasks.bank.escrow_release.side_effect = ServiceError(
        "central_bank_unavailable", "injected", 502, {}
    )
    with pytest.raises(ServiceError):
        await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
    assert_pending(native_tasks, "t-1", _APPROVAL_OPERATION_ID, source)
    native_tasks.bank.escrow_release.side_effect = native_tasks.persist_release
    completed = await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
    assert completed["operation_state"] == "completed"
    assert (
        native_tasks.assert_settlement(
            "t-1", command=command, status=target, recipient=recipient, event_type=event
        )
        == _APPROVAL_OPERATION_ID
    )
    calls = native_tasks.bank.escrow_release.await_args_list
    assert len(calls) == 2
    assert calls[0].kwargs == calls[1].kwargs
    assert calls[0].kwargs["operation_id"] == _APPROVAL_OPERATION_ID
    assert calls[0].kwargs["recipient_account_id"] == recipient
    assert (
        await complete_bank_operation(native_tasks.store, native_tasks.escrow, completed)
        == completed
    )
    assert native_tasks.bank.escrow_release.await_count == 2


@pytest.mark.unit
async def test_repeated_delivery_failure_keeps_native_ownership(
    native_tasks: NativeTaskFixture,
) -> None:
    prepare_submitted(native_tasks, "t-1")
    claimed = claim_approval(native_tasks, "t-1", _APPROVAL_OPERATION_ID)
    native_tasks.bank.escrow_release.side_effect = ServiceError(
        "central_bank_unavailable", "injected", 502, {}
    )
    for _ in range(2):
        with pytest.raises(ServiceError):
            await complete_bank_operation(native_tasks.store, native_tasks.escrow, claimed)
        assert_pending(native_tasks, "t-1", _APPROVAL_OPERATION_ID, "submitted")
    assert (
        native_tasks.store.writer.connection.execute(
            "SELECT COUNT(*) FROM board_task_operations "
            "WHERE task_id='t-1' AND command='approve_task'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.unit
async def test_disputed_state_has_no_implicit_bank_retry(native_tasks: NativeTaskFixture) -> None:
    prepare_submitted(native_tasks, "t-1")
    native_tasks.dispute("t-1", rebuttal_seconds=3600)
    task = native_tasks.task("t-1")
    result = await native_tasks.evaluator(None).evaluate_deadline(task)
    assert result == task
    assert result["status"] == "disputed"
    native_tasks.bank.escrow_release.assert_not_awaited()
    native_tasks.bank.escrow_split.assert_not_awaited()

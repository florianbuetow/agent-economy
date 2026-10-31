"""Deadline transitions against real Gateway-owned task and settlement facts."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from tests.fakes.native_task_fixture import START_US, TaskTerms, native_task_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.fakes.native_task_fixture import NativeTaskFixture

pytestmark = pytest.mark.unit
_TERMS = TaskTerms(100, 3600, 600, 3600, 3600)
_TASK_1 = "t-00000000-0000-4000-8000-000000000301"
_TASK_2 = "t-00000000-0000-4000-8000-000000000302"


@pytest.fixture(name="native_tasks")
def native_tasks_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[NativeTaskFixture]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        yield scenario


def test_compute_deadline_valid() -> None:
    assert DeadlineEvaluator.compute_deadline("2025-01-01T00:00:00Z", 60) == "2025-01-01T00:01:00Z"


def test_compute_deadline_none_base() -> None:
    assert DeadlineEvaluator.compute_deadline(None, 60) is None


async def test_evaluate_deadline_terminal_status_skipped(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task(_TASK_1, _TERMS)
    native_tasks.bid(_TASK_1, 100)
    native_tasks.accept(_TASK_1, 100)
    native_tasks.submit(_TASK_1)
    await native_tasks.approve(_TASK_1)
    task = native_tasks.task(_TASK_1)
    before_events = native_tasks.store.writer.get_total_events()
    native_tasks.bank.escrow_release.reset_mock()
    native_tasks.clock.at_seconds(7200)

    result = await native_tasks.evaluator(None).evaluate_deadline(task)

    assert result == task
    assert result["status"] == "approved"
    assert native_tasks.store.writer.get_total_events() == before_events
    native_tasks.bank.escrow_release.assert_not_awaited()


async def test_evaluate_deadline_open_no_bids_expired(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task(_TASK_1, _TERMS)
    native_tasks.clock.at_seconds(3600)
    result = await native_tasks.evaluator(None).evaluate_deadline(native_tasks.task(_TASK_1))
    assert result["status"] == "expired"
    operation_id = native_tasks.assert_settlement(
        _TASK_1,
        command="expire_bidding",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )
    native_tasks.bank.escrow_release.assert_awaited_once_with(
        escrow_id=f"esc-{_TASK_1}",
        recipient_account_id=native_tasks.poster_id,
        operation_id=operation_id,
        source_reference=f"task:{_TASK_1}:expired",
    )


async def test_evaluate_deadline_open_with_bids_expires(native_tasks: NativeTaskFixture) -> None:
    """Native bids retain the poster's acceptance window, then refund at its cutoff."""
    native_tasks.open_task(_TASK_1, _TERMS)
    native_tasks.bid(_TASK_1, 100)
    evaluator = native_tasks.evaluator(None)
    native_tasks.clock.at_seconds(3600)
    during_window = await evaluator.evaluate_deadline(native_tasks.task(_TASK_1))
    assert during_window["status"] == "open"
    assert during_window["bid_count"] == 1
    native_tasks.bank.escrow_release.assert_not_awaited()
    native_tasks.clock.at_seconds(4200)
    result = await evaluator.evaluate_deadline(during_window)
    assert result["status"] == "expired"
    native_tasks.assert_settlement(
        _TASK_1,
        command="expire_acceptance",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )
    native_tasks.bank.escrow_release.assert_awaited_once()


async def test_legacy_timing_contract_keeps_original_acceptance_cutoff(
    native_tasks: NativeTaskFixture,
) -> None:
    """A labeled historical sidecar fixture has no newly invented acceptance window."""
    native_tasks.open_task(_TASK_1, _TERMS)
    native_tasks.seed_legacy_timing_contract(_TASK_1)
    native_tasks.bid(_TASK_1, 100)
    native_tasks.clock.at_seconds(3600)
    result = await native_tasks.evaluator(None).evaluate_deadline(native_tasks.task(_TASK_1))
    assert result["status"] == "expired"
    assert result["acceptance_window_seconds"] is None
    native_tasks.assert_settlement(
        _TASK_1,
        command="expire_acceptance",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )


async def test_evaluate_deadline_accepted_past_execution(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task(_TASK_1, _TERMS)
    native_tasks.bid(_TASK_1, 100)
    native_tasks.accept(_TASK_1, 100)
    native_tasks.clock.at_seconds(3600)
    result = await native_tasks.evaluator(None).evaluate_deadline(native_tasks.task(_TASK_1))
    assert result["status"] == "expired"
    operation_id = native_tasks.assert_settlement(
        _TASK_1,
        command="expire_execution",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )
    native_tasks.bank.escrow_release.assert_awaited_once_with(
        escrow_id=f"esc-{_TASK_1}",
        recipient_account_id=native_tasks.poster_id,
        operation_id=operation_id,
        source_reference=f"task:{_TASK_1}:expired",
    )


async def test_evaluate_deadline_submitted_past_review(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task(_TASK_1, _TERMS)
    native_tasks.bid(_TASK_1, 100)
    native_tasks.accept(_TASK_1, 100)
    native_tasks.submit(_TASK_1)
    native_tasks.clock.at_seconds(3600)
    result = await native_tasks.evaluator(None).evaluate_deadline(native_tasks.task(_TASK_1))
    assert result["status"] == "approved"
    operation_id = native_tasks.assert_settlement(
        _TASK_1,
        command="auto_approve",
        status="approved",
        recipient=native_tasks.worker_id,
        event_type="task.auto_approved",
    )
    native_tasks.bank.escrow_release.assert_awaited_once_with(
        escrow_id=f"esc-{_TASK_1}",
        recipient_account_id=native_tasks.worker_id,
        operation_id=operation_id,
        source_reference=f"task:{_TASK_1}:approved",
    )


async def test_evaluate_deadline_not_past_deadline(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task(_TASK_1, _TERMS)
    task = native_tasks.task(_TASK_1)
    before_events = native_tasks.store.writer.get_total_events()
    native_tasks.clock.at_seconds(1800)
    result = await native_tasks.evaluator(None).evaluate_deadline(task)
    assert result == task
    assert result["status"] == "open"
    assert native_tasks.store.writer.get_total_events() == before_events
    native_tasks.bank.escrow_release.assert_not_awaited()


async def test_evaluate_deadline_retries_pending_escrow(native_tasks: NativeTaskFixture) -> None:
    """A failed automatic delivery retries the exact stored owner, not a bare flag."""
    native_tasks.open_task(_TASK_1, _TERMS)
    native_tasks.clock.at_seconds(3600)
    native_tasks.bank.escrow_release.side_effect = ServiceError(
        "bank_unavailable", "injected", 502, {}
    )
    evaluator = native_tasks.evaluator(None)
    pending = await evaluator.evaluate_deadline(native_tasks.task(_TASK_1))
    assert pending["status"] == "open"
    operation_id = operation_id_for(
        "task-deadline", (_TASK_1, "expire_bidding", "0", str(START_US + 3_600_000_000))
    )
    assert pending["pending_transition"]["operation_id"] == operation_id
    native_tasks.bank.escrow_release.side_effect = native_tasks.persist_release
    native_tasks.clock.at_seconds(3601)
    result = await evaluator.evaluate_deadline(pending)
    assert result["status"] == "expired"
    assert (
        native_tasks.assert_settlement(
            _TASK_1,
            command="expire_bidding",
            status="expired",
            recipient=native_tasks.poster_id,
            event_type="task.expired",
        )
        == operation_id
    )
    assert native_tasks.bank.escrow_release.await_count == 2
    assert all(
        call.kwargs["operation_id"] == operation_id
        for call in native_tasks.bank.escrow_release.await_args_list
    )


async def test_bare_pending_flag_cannot_authorize_early_release(
    native_tasks: NativeTaskFixture,
) -> None:
    native_tasks.open_task(_TASK_1, _TERMS)
    actual = native_tasks.task(_TASK_1)
    # Deliberately inconsistent read fixture: a flag is not a persisted native owner.
    flagged = dict(actual)
    flagged["escrow_pending"] = 1
    native_tasks.clock.at_seconds(1800)
    result = await native_tasks.evaluator(None).evaluate_deadline(flagged)
    assert result["status"] == "open"
    assert native_tasks.task(_TASK_1) == actual
    native_tasks.bank.escrow_release.assert_not_awaited()


async def test_evaluate_deadlines_batch_processes_all(native_tasks: NativeTaskFixture) -> None:
    native_tasks.open_task(_TASK_1, _TERMS)
    native_tasks.open_task(_TASK_2, _TERMS)
    native_tasks.bid(_TASK_2, 100)
    native_tasks.accept(_TASK_2, 100)
    native_tasks.clock.at_seconds(3600)
    result = await native_tasks.evaluator(None).evaluate_deadlines_batch(
        [native_tasks.task(_TASK_1), native_tasks.task(_TASK_2)]
    )
    assert len(result) == 2
    assert result[0]["status"] == "expired"
    assert result[1]["status"] == "expired"
    for task_id, command in ((_TASK_1, "expire_bidding"), (_TASK_2, "expire_execution")):
        native_tasks.assert_settlement(
            task_id,
            command=command,
            status="expired",
            recipient=native_tasks.poster_id,
            event_type="task.expired",
        )

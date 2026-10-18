"""Real periodic deadline sweeps with explicit clocks and persisted effects."""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from tests.fakes.native_task_fixture import TaskTerms, native_task_context

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from service_auth.platform import PlatformAgent

    from task_board_service.services.deadline_evaluator import DeadlineEvaluator
    from tests.fakes.native_task_fixture import NativeTaskFixture

pytestmark = pytest.mark.unit
_SHORT_TERMS = TaskTerms(100, 1, 1, 1, 1)
_LONG_TERMS = TaskTerms(100, 3600, 600, 3600, 3600)


@pytest.fixture(name="native_tasks")
def native_tasks_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[NativeTaskFixture]:
    with native_task_context(tmp_path, monkeypatch) as scenario:
        yield scenario


async def _run_sweep_briefly(evaluator: DeadlineEvaluator, interval_seconds: int) -> None:
    """Keep asyncio timers live while business time comes from the shared fixture clock."""
    sweep_task = asyncio.create_task(evaluator.run_periodic_sweep(interval_seconds))
    try:
        await asyncio.sleep(interval_seconds + 0.5)
    finally:
        sweep_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep_task


async def test_sweep_expires_open_task_without_any_api_read(
    native_tasks: NativeTaskFixture,
) -> None:
    native_tasks.open_task("t-1", _SHORT_TERMS)
    native_tasks.clock.at_seconds(5)
    await _run_sweep_briefly(native_tasks.evaluator(None), interval_seconds=1)
    operation_id = native_tasks.assert_settlement(
        "t-1",
        command="expire_bidding",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )
    native_tasks.bank.escrow_release.assert_awaited_once_with(
        escrow_id="esc-t-1",
        recipient_account_id=native_tasks.poster_id,
        operation_id=operation_id,
        source_reference="task:t-1:expired",
    )


async def test_sweep_visits_accepted_submitted_and_disputed_statuses(
    native_tasks: NativeTaskFixture,
) -> None:
    for task_id in ("t-open", "t-accepted", "t-submitted", "t-disputed"):
        native_tasks.open_task(task_id, _SHORT_TERMS)
    for task_id in ("t-accepted", "t-submitted", "t-disputed"):
        native_tasks.bid(task_id, 90)
        native_tasks.accept(task_id, 90)
    for task_id in ("t-submitted", "t-disputed"):
        native_tasks.submit(task_id)
    native_tasks.dispute("t-disputed", rebuttal_seconds=1)
    platform = AsyncMock()
    platform.trigger_ruling.return_value = {"status": "ruled"}
    native_tasks.clock.at_seconds(5)

    await _run_sweep_briefly(native_tasks.evaluator(cast("PlatformAgent", platform)), 1)

    native_tasks.assert_settlement(
        "t-open",
        command="expire_bidding",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )
    native_tasks.assert_settlement(
        "t-accepted",
        command="expire_execution",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )
    native_tasks.assert_settlement(
        "t-submitted",
        command="auto_approve",
        status="approved",
        recipient=native_tasks.worker_id,
        event_type="task.auto_approved",
    )
    platform.trigger_ruling.assert_awaited_once_with("clm-t-disputed")
    # Trigger acknowledgment is not a persisted ruling or a judging proof.
    assert native_tasks.task("t-disputed")["status"] == "disputed"


async def test_sweep_leaves_task_not_past_deadline_untouched(
    native_tasks: NativeTaskFixture,
) -> None:
    native_tasks.open_task("t-1", _LONG_TERMS)
    before = native_tasks.task("t-1")
    before_events = native_tasks.store.writer.get_total_events()
    await _run_sweep_briefly(native_tasks.evaluator(None), interval_seconds=1)
    assert native_tasks.task("t-1") == before
    assert native_tasks.store.writer.get_total_events() == before_events
    native_tasks.bank.escrow_release.assert_not_awaited()


async def test_sweep_is_idempotent_against_a_concurrent_lazy_evaluation(
    native_tasks: NativeTaskFixture,
) -> None:
    """Overlapping deliveries reuse one claim and produce one settlement/event."""
    native_tasks.open_task("t-1", _SHORT_TERMS)
    native_tasks.clock.at_seconds(5)
    evaluator = native_tasks.evaluator(None)
    task = native_tasks.task("t-1")
    both_entered = asyncio.Event()
    deliveries = 0

    async def _overlapping_release(
        escrow_id: str,
        recipient_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        nonlocal deliveries
        pending = native_tasks.task("t-1")
        assert pending["status"] == "open"
        assert pending["pending_transition"]["operation_id"] == operation_id
        deliveries += 1
        if deliveries == 2:
            both_entered.set()
        await both_entered.wait()
        return await native_tasks.persist_release(
            escrow_id, recipient_account_id, operation_id, source_reference
        )

    native_tasks.bank.escrow_release.side_effect = _overlapping_release
    await asyncio.wait_for(
        asyncio.gather(
            evaluator.evaluate_deadline(dict(task)), evaluator.evaluate_deadline(dict(task))
        ),
        timeout=5,
    )
    operation_id = native_tasks.assert_settlement(
        "t-1",
        command="expire_bidding",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )
    assert deliveries == native_tasks.bank.escrow_release.await_count == 2
    assert all(
        call.kwargs
        == {
            "escrow_id": "esc-t-1",
            "recipient_account_id": native_tasks.poster_id,
            "operation_id": operation_id,
            "source_reference": "task:t-1:expired",
        }
        for call in native_tasks.bank.escrow_release.await_args_list
    )
    await evaluator.evaluate_deadline(native_tasks.task("t-1"))
    assert native_tasks.bank.escrow_release.await_count == 2


async def test_sweep_survives_a_failing_task_and_continues_with_others(
    native_tasks: NativeTaskFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_tasks.open_task("t-bad", _SHORT_TERMS)
    native_tasks.open_task("t-good", _SHORT_TERMS)
    native_tasks.clock.at_seconds(5)
    evaluator = native_tasks.evaluator(None)
    original_evaluate = evaluator.evaluate_deadline

    async def _flaky_evaluate(task: dict[str, Any]) -> dict[str, Any]:
        if task["task_id"] == "t-bad":
            raise RuntimeError("simulated evaluation failure")
        return await original_evaluate(task)

    monkeypatch.setattr(evaluator, "evaluate_deadline", _flaky_evaluate)
    await _run_sweep_briefly(evaluator, interval_seconds=1)
    native_tasks.assert_settlement(
        "t-good",
        command="expire_bidding",
        status="expired",
        recipient=native_tasks.poster_id,
        event_type="task.expired",
    )
    assert native_tasks.task("t-bad")["status"] == "open"
    assert native_tasks.task("t-bad")["pending_transition"] is None

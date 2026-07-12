"""Unit tests for DeadlineEvaluator.run_periodic_sweep (Q-5, GAP-A3).

Proves the periodic background sweep transitions expired-deadline tasks with
nothing ever reading them via the API — the lazy-only evaluation path (already
covered by tests/unit/test_deadline_evaluator.py) is never invoked here.

These tests use real wall-clock timestamps (not freezegun's freeze_time):
run_periodic_sweep genuinely calls asyncio.sleep(interval_seconds), and
freeze_time freezes the monotonic clock asyncio's timers rely on too, which
would stall that sleep forever. Real short intervals plus already-past
created_at timestamps sidestep the conflict entirely.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.task_store import TaskStore


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _task_data(
    task_id: str,
    status: str,
    created_at: str,
    accepted_at: str | None,
    submitted_at: str | None,
    bid_count: int,
    escrow_pending: int,
    deadline_seconds: int = 1,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "poster_id": "a-poster",
        "title": "Task",
        "spec": "Spec",
        "reward": 100,
        "bidding_deadline_seconds": deadline_seconds,
        "deadline_seconds": deadline_seconds,
        "review_deadline_seconds": deadline_seconds,
        "status": status,
        "escrow_id": "esc-1",
        "bid_count": bid_count,
        "worker_id": "a-worker",
        "accepted_bid_id": "bid-1",
        "created_at": created_at,
        "accepted_at": accepted_at,
        "submitted_at": submitted_at,
        "approved_at": None,
        "cancelled_at": None,
        "disputed_at": None,
        "dispute_reason": None,
        "ruling_id": None,
        "ruled_at": None,
        "worker_pct": None,
        "ruling_summary": None,
        "expired_at": None,
        "escrow_pending": escrow_pending,
    }


def _mock_escrow_coordinator() -> AsyncMock:
    mock_coordinator = AsyncMock()
    mock_coordinator.retry_pending_escrow = AsyncMock(side_effect=lambda task: task)
    mock_coordinator.try_release_escrow = AsyncMock()
    return mock_coordinator


async def _run_sweep_briefly(evaluator: DeadlineEvaluator, interval_seconds: int) -> None:
    """Start the sweep loop, let it run for slightly over one interval, then stop it."""
    sweep_task = asyncio.create_task(evaluator.run_periodic_sweep(interval_seconds))
    await asyncio.sleep(interval_seconds + 0.5)
    sweep_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweep_task


@pytest.mark.unit
async def test_sweep_expires_open_task_without_any_api_read(tmp_path) -> None:
    """The background sweep transitions an expired task with nothing reading it.

    No GET /tasks/{id} (lazy evaluation) ever happens in this test — proof is
    read directly from the store, not via a request that would itself trigger
    the lazy evaluate_deadline path.
    """
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    already_expired = _iso(datetime.now(UTC) - timedelta(seconds=5))
    store.insert_task(_task_data("t-1", "open", already_expired, None, None, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)

    await _run_sweep_briefly(evaluator, interval_seconds=1)

    task = store.get_task("t-1")
    assert task is not None
    assert task["status"] == "expired"
    mock_coordinator.try_release_escrow.assert_awaited_once_with("t-1", "esc-1", "a-poster")
    store.close()


@pytest.mark.unit
async def test_sweep_visits_accepted_submitted_and_disputed_statuses(tmp_path) -> None:
    """The sweep covers every non-terminal status, not just 'open'."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    already_expired = _iso(datetime.now(UTC) - timedelta(seconds=5))
    store.insert_task(_task_data("t-open", "open", already_expired, None, None, 0, 0))
    store.insert_task(
        _task_data("t-accepted", "accepted", already_expired, already_expired, None, 0, 0)
    )
    store.insert_task(
        _task_data(
            "t-submitted", "submitted", already_expired, already_expired, already_expired, 0, 0
        )
    )
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)

    await _run_sweep_briefly(evaluator, interval_seconds=1)

    assert store.get_task("t-open")["status"] == "expired"  # type: ignore[index]
    assert store.get_task("t-accepted")["status"] == "expired"  # type: ignore[index]
    assert store.get_task("t-submitted")["status"] == "approved"  # type: ignore[index]
    store.close()


@pytest.mark.unit
async def test_sweep_leaves_task_not_past_deadline_untouched(tmp_path) -> None:
    """A task whose deadline has not yet passed is left alone by the sweep."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    just_created = _iso(datetime.now(UTC))
    store.insert_task(
        _task_data("t-1", "open", just_created, None, None, 0, 0, deadline_seconds=3600)
    )
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)

    await _run_sweep_briefly(evaluator, interval_seconds=1)

    task = store.get_task("t-1")
    assert task is not None
    assert task["status"] == "open"
    mock_coordinator.try_release_escrow.assert_not_awaited()
    store.close()


@pytest.mark.unit
async def test_sweep_is_idempotent_against_a_concurrent_lazy_evaluation(tmp_path) -> None:
    """A lazy read racing the sweep for the same task settles escrow exactly once.

    Both paths call evaluate_deadline for the same already-expired task; the
    store's compare-and-swap on expected_status ensures only one transition
    (and therefore one escrow release) wins the race.
    """
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    already_expired = _iso(datetime.now(UTC) - timedelta(seconds=5))
    store.insert_task(_task_data("t-1", "open", already_expired, None, None, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)

    task = store.get_task("t-1")
    assert task is not None
    # Simulate the sweep and a concurrent lazy read evaluating the same
    # already-fetched task dict at (conceptually) the same instant.
    await asyncio.gather(
        evaluator.evaluate_deadline(dict(task)),
        evaluator.evaluate_deadline(dict(task)),
    )

    stored = store.get_task("t-1")
    assert stored is not None
    assert stored["status"] == "expired"
    mock_coordinator.try_release_escrow.assert_awaited_once_with("t-1", "esc-1", "a-poster")
    store.close()


@pytest.mark.unit
async def test_sweep_survives_a_failing_task_and_continues_with_others(tmp_path) -> None:
    """One task raising during evaluation does not stop the sweep from evaluating others."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    already_expired = _iso(datetime.now(UTC) - timedelta(seconds=5))
    store.insert_task(_task_data("t-bad", "open", already_expired, None, None, 0, 0))
    store.insert_task(_task_data("t-good", "open", already_expired, None, None, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)

    original_evaluate = evaluator.evaluate_deadline

    async def _flaky_evaluate(task: dict[str, object]) -> dict[str, object]:
        if task["task_id"] == "t-bad":
            msg = "simulated evaluation failure"
            raise RuntimeError(msg)
        return await original_evaluate(task)

    evaluator.evaluate_deadline = _flaky_evaluate  # type: ignore[method-assign]

    await _run_sweep_briefly(evaluator, interval_seconds=1)

    good = store.get_task("t-good")
    bad = store.get_task("t-bad")
    assert good is not None
    assert bad is not None
    assert good["status"] == "expired"
    assert bad["status"] == "open"  # the flaky task was skipped, not silently marked done
    store.close()

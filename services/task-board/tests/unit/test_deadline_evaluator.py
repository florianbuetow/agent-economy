"""Unit tests for DeadlineEvaluator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from task_board_service.services.deadline_evaluator import DeadlineEvaluator
from task_board_service.services.task_store import TaskStore


def _task_data(
    task_id: str,
    status: str,
    created_at: str,
    accepted_at: str | None,
    submitted_at: str | None,
    bid_count: int,
    escrow_pending: int,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "poster_id": "a-poster",
        "title": "Task",
        "spec": "Spec",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 3600,
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


def _timestamp(value: str) -> str:
    return datetime.fromisoformat(value).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _mock_escrow_coordinator() -> AsyncMock:
    mock_coordinator = AsyncMock()
    mock_coordinator.retry_pending_escrow = AsyncMock(side_effect=lambda task: task)
    mock_coordinator.try_release_escrow = AsyncMock()
    return mock_coordinator


@pytest.mark.unit
def test_compute_deadline_valid() -> None:
    """compute_deadline adds seconds to base timestamp."""
    result = DeadlineEvaluator.compute_deadline("2025-01-01T00:00:00Z", 60)
    assert result == "2025-01-01T00:01:00Z"


@pytest.mark.unit
def test_compute_deadline_none_base() -> None:
    """compute_deadline returns None with missing base timestamp."""
    assert DeadlineEvaluator.compute_deadline(None, 60) is None


@pytest.mark.unit
async def test_evaluate_deadline_terminal_status_skipped(tmp_path) -> None:
    """Terminal status bypasses deadline logic."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    now = _timestamp("2025-01-01T00:00:00")
    store.insert_task(_task_data("t-1", "approved", now, now, now, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)
    task = store.get_task("t-1")
    assert task is not None

    result = await evaluator.evaluate_deadline(task)

    assert result["status"] == "approved"
    mock_coordinator.retry_pending_escrow.assert_not_awaited()
    store.close()


@pytest.mark.unit
async def test_evaluate_deadline_open_no_bids_expired(tmp_path) -> None:
    """Open task with no bids expires after bidding deadline."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    created = _timestamp("2025-01-01T00:00:00")
    store.insert_task(_task_data("t-1", "open", created, None, None, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)
    task = store.get_task("t-1")
    assert task is not None

    with freeze_time("2025-01-01 01:00:00"):
        result = await evaluator.evaluate_deadline(task)

    assert result["status"] == "expired"
    mock_coordinator.try_release_escrow.assert_awaited_once_with("t-1", "esc-1", "a-poster")
    store.close()


@pytest.mark.unit
async def test_evaluate_deadline_open_with_bids_not_expired(tmp_path) -> None:
    """Open task with bids stays open even past bidding deadline."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    created = _timestamp("2025-01-01T00:00:00")
    store.insert_task(_task_data("t-1", "open", created, None, None, 1, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)
    task = store.get_task("t-1")
    assert task is not None

    with freeze_time("2025-01-01 01:10:00"):
        result = await evaluator.evaluate_deadline(task)

    assert result["status"] == "open"
    mock_coordinator.try_release_escrow.assert_not_awaited()
    store.close()


@pytest.mark.unit
async def test_evaluate_deadline_accepted_past_execution(tmp_path) -> None:
    """Accepted task expires after execution deadline."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    created = _timestamp("2025-01-01T00:00:00")
    accepted = _timestamp("2025-01-01T00:00:00")
    store.insert_task(_task_data("t-1", "accepted", created, accepted, None, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)
    task = store.get_task("t-1")
    assert task is not None

    with freeze_time("2025-01-01 01:00:00"):
        result = await evaluator.evaluate_deadline(task)

    assert result["status"] == "expired"
    mock_coordinator.try_release_escrow.assert_awaited_once_with("t-1", "esc-1", "a-poster")
    store.close()


@pytest.mark.unit
async def test_evaluate_deadline_submitted_past_review(tmp_path) -> None:
    """Submitted task auto-approves after review deadline."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    created = _timestamp("2025-01-01T00:00:00")
    submitted = _timestamp("2025-01-01T00:00:00")
    store.insert_task(_task_data("t-1", "submitted", created, created, submitted, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)
    task = store.get_task("t-1")
    assert task is not None

    with freeze_time("2025-01-01 01:00:00"):
        result = await evaluator.evaluate_deadline(task)

    assert result["status"] == "approved"
    mock_coordinator.try_release_escrow.assert_awaited_once_with("t-1", "esc-1", "a-worker")
    store.close()


@pytest.mark.unit
async def test_evaluate_deadline_not_past_deadline(tmp_path) -> None:
    """No transition occurs before deadline."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    created = _timestamp("2025-01-01T00:00:00")
    store.insert_task(_task_data("t-1", "open", created, None, None, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)
    task = store.get_task("t-1")
    assert task is not None

    with freeze_time("2025-01-01 00:30:00"):
        result = await evaluator.evaluate_deadline(task)

    assert result["status"] == "open"
    mock_coordinator.try_release_escrow.assert_not_awaited()
    store.close()


@pytest.mark.unit
async def test_evaluate_deadline_retries_pending_escrow(tmp_path) -> None:
    """Pending escrow triggers retry before normal deadline checks."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    created = _timestamp("2025-01-01T00:00:00")
    store.insert_task(_task_data("t-1", "open", created, None, None, 0, 1))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)
    task = store.get_task("t-1")
    assert task is not None

    with freeze_time("2025-01-01 00:30:00"):
        _result = await evaluator.evaluate_deadline(task)

    mock_coordinator.retry_pending_escrow.assert_awaited_once()
    store.close()


@pytest.mark.unit
async def test_evaluate_deadlines_batch_processes_all(tmp_path) -> None:
    """Batch evaluator processes every provided task."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    created = _timestamp("2025-01-01T00:00:00")
    accepted = _timestamp("2025-01-01T00:00:00")
    store.insert_task(_task_data("t-1", "open", created, None, None, 0, 0))
    store.insert_task(_task_data("t-2", "accepted", created, accepted, None, 0, 0))
    mock_coordinator = _mock_escrow_coordinator()
    evaluator = DeadlineEvaluator(store=store, escrow_coordinator=mock_coordinator)
    task_one = store.get_task("t-1")
    task_two = store.get_task("t-2")
    assert task_one is not None
    assert task_two is not None

    with freeze_time("2025-01-01 01:00:00"):
        result = await evaluator.evaluate_deadlines_batch([task_one, task_two])

    assert len(result) == 2
    assert result[0]["status"] == "expired"
    assert result[1]["status"] == "expired"
    store.close()

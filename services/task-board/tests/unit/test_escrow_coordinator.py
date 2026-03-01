"""Unit tests for EscrowCoordinator."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.escrow_coordinator import EscrowCoordinator
from task_board_service.services.task_store import TaskStore


def _task_data(
    task_id: str,
    status: str,
    escrow_pending: int,
    worker_id: str | None,
) -> dict[str, object]:
    timestamp = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return {
        "task_id": task_id,
        "poster_id": "a-poster",
        "title": "Task",
        "spec": "Spec",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 7200,
        "review_deadline_seconds": 1800,
        "status": status,
        "escrow_id": "esc-1",
        "bid_count": 0,
        "worker_id": worker_id,
        "accepted_bid_id": None,
        "created_at": timestamp,
        "accepted_at": None,
        "submitted_at": None,
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


@pytest.mark.unit
async def test_release_escrow_success(tmp_path) -> None:
    """release_escrow calls Central Bank release endpoint."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)

    await coordinator.release_escrow("esc-1", "a-recipient")

    mock_bank.escrow_release.assert_awaited_once_with(
        escrow_id="esc-1",
        recipient_account_id="a-recipient",
    )
    store.close()


@pytest.mark.unit
async def test_release_escrow_service_error_propagates(tmp_path) -> None:
    """release_escrow re-raises ServiceError unchanged."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    expected = ServiceError("CENTRAL_BANK_UNAVAILABLE", "fail", 502, {})
    mock_bank = AsyncMock()
    mock_bank.escrow_release = AsyncMock(side_effect=expected)
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)

    with pytest.raises(ServiceError) as exc_info:
        await coordinator.release_escrow("esc-1", "a-recipient")

    assert exc_info.value is expected
    store.close()


@pytest.mark.unit
async def test_release_escrow_generic_error_wraps(tmp_path) -> None:
    """release_escrow wraps generic exceptions as CENTRAL_BANK_UNAVAILABLE."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    mock_bank = AsyncMock()
    mock_bank.escrow_release = AsyncMock(side_effect=RuntimeError("boom"))
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)

    with pytest.raises(ServiceError) as exc_info:
        await coordinator.release_escrow("esc-1", "a-recipient")

    assert exc_info.value.error == "CENTRAL_BANK_UNAVAILABLE"
    assert exc_info.value.status_code == 502
    store.close()


@pytest.mark.unit
async def test_split_escrow_success(tmp_path) -> None:
    """split_escrow calls Central Bank split endpoint."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)

    await coordinator.split_escrow("esc-1", "a-worker", "a-poster", 65)

    mock_bank.escrow_split.assert_awaited_once_with(
        escrow_id="esc-1",
        worker_account_id="a-worker",
        poster_account_id="a-poster",
        worker_pct=65,
    )
    store.close()


@pytest.mark.unit
async def test_split_escrow_service_error_propagates(tmp_path) -> None:
    """split_escrow re-raises ServiceError unchanged."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    expected = ServiceError("CENTRAL_BANK_UNAVAILABLE", "fail", 502, {})
    mock_bank = AsyncMock()
    mock_bank.escrow_split = AsyncMock(side_effect=expected)
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)

    with pytest.raises(ServiceError) as exc_info:
        await coordinator.split_escrow("esc-1", "a-worker", "a-poster", 65)

    assert exc_info.value is expected
    store.close()


@pytest.mark.unit
async def test_split_escrow_generic_error_wraps(tmp_path) -> None:
    """split_escrow wraps generic exceptions as CENTRAL_BANK_UNAVAILABLE."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    mock_bank = AsyncMock()
    mock_bank.escrow_split = AsyncMock(side_effect=RuntimeError("boom"))
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)

    with pytest.raises(ServiceError) as exc_info:
        await coordinator.split_escrow("esc-1", "a-worker", "a-poster", 65)

    assert exc_info.value.error == "CENTRAL_BANK_UNAVAILABLE"
    assert exc_info.value.status_code == 502
    store.close()


@pytest.mark.unit
async def test_try_release_escrow_success(tmp_path) -> None:
    """try_release_escrow clears escrow_pending on successful release."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "expired", 1, "a-worker"))
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)

    await coordinator.try_release_escrow("t-1", "esc-1", "a-recipient")

    updated = store.get_task("t-1")
    assert updated is not None
    assert updated["escrow_pending"] == 0
    store.close()


@pytest.mark.unit
async def test_try_release_escrow_failure(tmp_path) -> None:
    """try_release_escrow marks escrow_pending when release fails."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "expired", 0, "a-worker"))
    mock_bank = AsyncMock()
    mock_bank.escrow_release = AsyncMock(
        side_effect=ServiceError("CENTRAL_BANK_UNAVAILABLE", "fail", 502, {})
    )
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)

    await coordinator.try_release_escrow("t-1", "esc-1", "a-recipient")

    updated = store.get_task("t-1")
    assert updated is not None
    assert updated["escrow_pending"] == 1
    store.close()


@pytest.mark.unit
async def test_retry_pending_escrow_not_pending(tmp_path) -> None:
    """retry_pending_escrow returns task unchanged when not pending."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)
    task = _task_data("t-1", "open", 0, "a-worker")

    result = await coordinator.retry_pending_escrow(task)

    assert result == task
    mock_bank.escrow_release.assert_not_awaited()
    store.close()


@pytest.mark.unit
async def test_retry_pending_escrow_expired_success(tmp_path) -> None:
    """retry_pending_escrow releases to poster for expired task."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "expired", 1, "a-worker"))
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)
    task = store.get_task("t-1")
    assert task is not None

    result = await coordinator.retry_pending_escrow(task)

    mock_bank.escrow_release.assert_awaited_once_with(
        escrow_id="esc-1",
        recipient_account_id="a-poster",
    )
    assert result["escrow_pending"] == 0
    updated = store.get_task("t-1")
    assert updated is not None
    assert updated["escrow_pending"] == 0
    store.close()


@pytest.mark.unit
async def test_retry_pending_escrow_approved_success(tmp_path) -> None:
    """retry_pending_escrow releases to worker for approved task."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "approved", 1, "a-worker"))
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)
    task = store.get_task("t-1")
    assert task is not None

    result = await coordinator.retry_pending_escrow(task)

    mock_bank.escrow_release.assert_awaited_once_with(
        escrow_id="esc-1",
        recipient_account_id="a-worker",
    )
    assert result["escrow_pending"] == 0
    updated = store.get_task("t-1")
    assert updated is not None
    assert updated["escrow_pending"] == 0
    store.close()


@pytest.mark.unit
async def test_retry_pending_escrow_failure_remains_pending(tmp_path) -> None:
    """retry_pending_escrow keeps pending flag set on release failure."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1", "approved", 1, "a-worker"))
    mock_bank = AsyncMock()
    mock_bank.escrow_release = AsyncMock(
        side_effect=ServiceError("CENTRAL_BANK_UNAVAILABLE", "fail", 502, {})
    )
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)
    task = store.get_task("t-1")
    assert task is not None

    result = await coordinator.retry_pending_escrow(task)

    assert result["escrow_pending"] == 1
    updated = store.get_task("t-1")
    assert updated is not None
    assert updated["escrow_pending"] == 1
    store.close()


@pytest.mark.unit
async def test_retry_pending_escrow_other_status(tmp_path) -> None:
    """retry_pending_escrow skips unsupported statuses."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    mock_bank = AsyncMock()
    coordinator = EscrowCoordinator(central_bank_client=mock_bank, store=store)
    task = _task_data("t-1", "disputed", 1, "a-worker")

    result = await coordinator.retry_pending_escrow(task)

    assert result == task
    mock_bank.escrow_release.assert_not_awaited()
    store.close()

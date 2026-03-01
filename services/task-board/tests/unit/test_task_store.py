"""Unit tests for TaskStore."""

from datetime import UTC, datetime

import pytest

from task_board_service.services.task_store import DuplicateBidError, TaskStore


def _task_data(task_id: str, status: str = "open") -> dict[str, object]:
    timestamp = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
    return {
        "task_id": task_id,
        "poster_id": "a-poster",
        "title": f"Task {task_id}",
        "spec": "Specification",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 7200,
        "review_deadline_seconds": 1800,
        "status": status,
        "escrow_id": f"esc-{task_id}",
        "bid_count": 0,
        "worker_id": None,
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
        "escrow_pending": 0,
    }


@pytest.mark.unit
def test_task_crud_and_counts(tmp_path) -> None:
    """Task operations persist, update, list, and count correctly."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1"))
    store.insert_task(_task_data("t-2", status="accepted"))

    task = store.get_task("t-1")
    assert task is not None
    assert task["task_id"] == "t-1"
    assert task["status"] == "open"

    changed = store.update_task("t-1", {"status": "submitted"}, expected_status=None)
    assert changed == 1

    changed_mismatch = store.update_task("t-2", {"status": "ruled"}, expected_status="open")
    assert changed_mismatch == 0

    listed = store.list_tasks(status=None, poster_id=None, worker_id=None, limit=None, offset=None)
    assert len(listed) == 2
    assert store.count_tasks() == 2

    grouped = store.count_tasks_by_status()
    assert grouped["submitted"] == 1
    assert grouped["accepted"] == 1
    store.close()


@pytest.mark.unit
def test_bid_operations(tmp_path) -> None:
    """Bid insert/get/list works and duplicate bids raise DuplicateBidError."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1"))

    store.insert_bid(
        {
            "bid_id": "bid-1",
            "task_id": "t-1",
            "bidder_id": "a-worker-1",
            "amount": 90,
            "submitted_at": datetime.now(UTC).isoformat(),
        }
    )

    task = store.get_task("t-1")
    assert task is not None
    assert task["bid_count"] == 1

    bid = store.get_bid("bid-1", "t-1")
    assert bid is not None
    assert bid["bidder_id"] == "a-worker-1"

    bids = store.get_bids_for_task("t-1")
    assert len(bids) == 1
    assert bids[0]["bid_id"] == "bid-1"

    with pytest.raises(DuplicateBidError):
        store.insert_bid(
            {
                "bid_id": "bid-2",
                "task_id": "t-1",
                "bidder_id": "a-worker-1",
                "amount": 85,
                "submitted_at": datetime.now(UTC).isoformat(),
            }
        )
    store.close()


@pytest.mark.unit
def test_asset_operations(tmp_path) -> None:
    """Asset insert/get/list/count works for a task."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.insert_task(_task_data("t-1"))

    store.insert_asset(
        {
            "asset_id": "asset-1",
            "task_id": "t-1",
            "uploader_id": "a-worker-1",
            "filename": "result.txt",
            "content_type": "text/plain",
            "size_bytes": 12,
            "content_hash": "abc123",
            "uploaded_at": datetime.now(UTC).isoformat(),
        }
    )

    asset = store.get_asset("asset-1", "t-1")
    assert asset is not None
    assert asset["filename"] == "result.txt"

    assets = store.get_assets_for_task("t-1")
    assert len(assets) == 1
    assert assets[0]["asset_id"] == "asset-1"

    assert store.count_assets("t-1") == 1
    store.close()


@pytest.mark.unit
def test_close_succeeds(tmp_path) -> None:
    """close() can be called without error."""
    store = TaskStore(db_path=str(tmp_path / "task-board.db"))
    store.close()

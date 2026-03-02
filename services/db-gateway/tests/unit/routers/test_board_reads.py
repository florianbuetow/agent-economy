"""Board read endpoint tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _register_agent(
    client: TestClient,
    agent_id: str | None = None,
    name: str = "Test",
) -> str:
    aid = agent_id or f"a-{uuid4()}"
    client.post(
        "/identity/agents",
        json={
            "agent_id": aid,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        },
    )
    return aid


def _setup_funded_account(
    client: TestClient,
    agent_id: str | None = None,
    balance: int = 500,
) -> str:
    aid = _register_agent(client, agent_id)
    data: dict[str, Any] = {
        "account_id": aid,
        "balance": balance,
        "created_at": "2026-02-28T10:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        data["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-02-28T10:00:00Z",
        }
    client.post("/bank/accounts", json=data)
    return aid


def _lock_escrow(
    client: TestClient,
    payer_id: str,
    amount: int = 100,
    task_id: str | None = None,
) -> tuple[str, str]:
    tid = task_id or f"t-{uuid4()}"
    eid = f"esc-{uuid4()}"
    client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": eid,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": tid,
            "created_at": "2026-02-28T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=tid),
        },
    )
    return eid, tid


def _create_task(
    client: TestClient,
    poster_id: str | None = None,
) -> tuple[str, str, str]:
    """Create a full task with all prerequisites. Returns (task_id, poster_id, escrow_id)."""
    pid = poster_id or _setup_funded_account(client)
    eid, tid = _lock_escrow(client, pid)
    client.post(
        "/board/tasks",
        json={
            "task_id": tid,
            "poster_id": pid,
            "title": "Test Task",
            "spec": "Build a login page",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 86400,
            "deadline_seconds": 172800,
            "review_deadline_seconds": 43200,
            "bidding_deadline": "2026-03-01T10:00:00Z",
            "escrow_id": eid,
            "created_at": "2026-02-28T10:15:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=tid),
        },
    )
    return tid, pid, eid


def _submit_bid(
    client: TestClient,
    task_id: str,
    bidder_id: str,
    bid_id: str | None = None,
) -> str:
    bid = bid_id or f"bid-{uuid4()}"
    client.post(
        "/board/bids",
        json={
            "bid_id": bid,
            "task_id": task_id,
            "bidder_id": bidder_id,
            "proposal": "I will build it",
            "submitted_at": "2026-02-28T11:00:00Z",
            "event": make_event(source="board", event_type="bid.submitted", task_id=task_id),
        },
    )
    return bid


def _record_asset(
    client: TestClient,
    task_id: str,
    uploader_id: str,
    asset_id: str | None = None,
    filename: str = "deliverable.zip",
) -> str:
    aid = asset_id or f"asset-{uuid4()}"
    client.post(
        "/board/assets",
        json={
            "asset_id": aid,
            "task_id": task_id,
            "uploader_id": uploader_id,
            "filename": filename,
            "content_type": "application/zip",
            "size_bytes": 1024,
            "storage_path": f"{task_id}/{filename}",
            "uploaded_at": "2026-02-28T14:00:00Z",
            "event": make_event(source="board", event_type="asset.uploaded", task_id=task_id),
        },
    )
    return aid


# ===========================================================================
# Board Read Endpoint Tests
# ===========================================================================


@pytest.mark.unit
class TestBoardReads:
    """Tests for board read endpoints."""

    def test_get_task_by_id(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/{id} returns the task record."""
        tid, pid, _eid = _create_task(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks/{tid}")

        assert response.status_code == 200
        data = response.json()
        assert data["task_id"] == tid
        assert data["title"] == "Test Task"
        assert data["status"] == "open"
        assert data["poster_id"] == pid

    def test_get_task_not_found(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/{id} returns 404 when missing."""
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks/t-nonexistent")

        assert response.status_code == 404
        assert response.json()["error"] == "task_not_found"

    def test_list_tasks_empty(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks returns empty list when no tasks exist."""
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks?limit=10")

        assert response.status_code == 200
        assert response.json() == {"tasks": []}

    def test_list_tasks_returns_all(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks returns all tasks."""
        _create_task(app_with_writer)
        _create_task(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks?limit=10")

        assert response.status_code == 200
        tasks = response.json()["tasks"]
        assert len(tasks) == 2

    def test_list_tasks_with_limit(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks?limit=1 respects limit."""
        _create_task(app_with_writer)
        _create_task(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks?limit=1")

        assert response.status_code == 200
        tasks = response.json()["tasks"]
        assert len(tasks) == 1

    def test_get_bids_for_task(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/{id}/bids returns bids."""
        tid, _pid, _eid = _create_task(app_with_writer)
        bidder1 = _register_agent(app_with_writer, name="Bidder1")
        bidder2 = _register_agent(app_with_writer, name="Bidder2")
        _submit_bid(app_with_writer, tid, bidder1)
        _submit_bid(app_with_writer, tid, bidder2)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks/{tid}/bids")

        assert response.status_code == 200
        bids = response.json()["bids"]
        assert len(bids) == 2
        assert "bid_id" in bids[0]
        assert "bidder_id" in bids[0]

    def test_get_bids_for_task_empty(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/{id}/bids returns empty list when no bids."""
        tid, _pid, _eid = _create_task(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks/{tid}/bids")

        assert response.status_code == 200
        assert response.json() == {"bids": []}

    def test_get_assets_for_task(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/{id}/assets returns assets."""
        tid, pid, _eid = _create_task(app_with_writer)
        _record_asset(app_with_writer, tid, pid, filename="file1.zip")
        _record_asset(app_with_writer, tid, pid, filename="file2.zip")
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks/{tid}/assets")

        assert response.status_code == 200
        assets = response.json()["assets"]
        assert len(assets) == 2
        assert "filename" in assets[0]
        assert "asset_id" in assets[0]

    def test_get_assets_for_task_empty(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/{id}/assets returns empty list when no assets."""
        tid, _pid, _eid = _create_task(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks/{tid}/assets")

        assert response.status_code == 200
        assert response.json() == {"assets": []}

    def test_count_tasks_zero(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/count returns zero when empty."""
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks/count")

        assert response.status_code == 200
        assert response.json() == {"count": 0}

    def test_count_tasks_after_creation(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/count reflects created tasks."""
        _create_task(app_with_writer)
        _create_task(app_with_writer)
        _create_task(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks/count")

        assert response.status_code == 200
        assert response.json() == {"count": 3}

    def test_count_tasks_by_status(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/count-by-status groups tasks by status."""
        _create_task(app_with_writer)
        _create_task(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks/count-by-status")

        assert response.status_code == 200
        data = response.json()
        assert data["open"] == 2

    def test_count_tasks_by_status_empty(self, app_with_writer: TestClient) -> None:
        """GET /board/tasks/count-by-status returns empty when no tasks."""
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks/count-by-status")

        assert response.status_code == 200
        assert response.json() == {}

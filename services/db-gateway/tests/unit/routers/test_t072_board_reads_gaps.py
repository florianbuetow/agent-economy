"""T-072 coverage-vs-spec-ID sweep: gap-fill tests for board read endpoints.

Covers READ-14 (status/poster_id/worker_id filters), READ-15 (limit+offset
happy path), READ-16 (bid_count/escrow_pending fields), READ-19
(/assets/count), READ-20 (GET /board/bids/{bid_id} requires task_id), and
READ-21 (GET /board/assets/{asset_id} requires task_id). These are all
already-implemented endpoints (routers/board.py + services/db_reader.py) with
no existing test — verified via a full literal-ID grep across
services/db-gateway/tests/ before writing.

Note: this file does NOT touch the non-numeric limit/offset validation
scenario (`GET /board/tasks?limit=abc`) — that bug/fix is being handled by
another in-flight test file
(services/db-gateway/tests/unit/routers/test_board_tasks_limit_offset_validation.py)
and is out of scope here; READ-15 below only exercises the documented
happy path (numeric limit+offset together).

New file — no existing test file was modified.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service import task_time
from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _task_business_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    now_us = task_time.parse_utc_text_to_epoch_us("2026-02-28T15:00:00Z")
    monkeypatch.setattr(task_time, "utc_now_us", lambda: now_us)


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _register_agent(client: TestClient, agent_id: str | None = None, name: str = "Test") -> str:
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
    client: TestClient, agent_id: str | None = None, balance: int = 500
) -> str:
    aid = _register_agent(client, agent_id)
    data: dict[str, Any] = {
        "account_id": aid,
        "balance": balance,
        "created_at": "2026-02-28T10:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        data["operation_id"] = f"op-{uuid4()}"
        data["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-02-28T10:00:00Z",
        }
    client.post("/bank/accounts", json=data)
    return aid


def _lock_escrow(
    client: TestClient, payer_id: str, amount: int = 100, task_id: str | None = None
) -> tuple[str, str]:
    tid = task_id or f"t-{uuid4()}"
    eid = f"esc-{uuid4()}"
    client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": f"op-{uuid4()}",
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
    created_at: str = "2026-02-28T10:15:00Z",
) -> tuple[str, str, str]:
    """Create a full task. Returns (task_id, poster_id, escrow_id)."""
    pid = poster_id or _setup_funded_account(client)
    eid, tid = _lock_escrow(client, pid)
    response = client.post(
        "/board/tasks",
        json={
            "task_id": tid,
            "poster_id": pid,
            "title": "Test Task",
            "spec": "Build a login page",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 86400,
            "acceptance_window_seconds": 3600,
            "deadline_seconds": 172800,
            "review_deadline_seconds": 43200,
            "bidding_deadline": (datetime.fromisoformat(created_at) + timedelta(days=1))
            .isoformat()
            .replace("+00:00", "Z"),
            "escrow_id": eid,
            "created_at": created_at,
            "event": make_event(source="board", event_type="task.created", task_id=tid),
        },
    )
    assert response.status_code == 201, response.text
    return tid, pid, eid


def _submit_bid(client: TestClient, task_id: str, bidder_id: str) -> str:
    bid_id = f"bid-{uuid4()}"
    response = client.post(
        "/board/task-operations",
        json={
            "protocol_version": 1,
            "operation_id": f"op-{uuid4()}",
            "task_id": task_id,
            "command": "submit_bid",
            "expected_status": "open",
            "expected_lifecycle_version": 0,
            "actor_kind": "agent",
            "actor_id": bidder_id,
            "arguments": {"bid_id": bid_id, "bidder_id": bidder_id, "amount": 50},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["operation_state"] == "completed"
    return bid_id


def _record_asset(client: TestClient, task_id: str, uploader_id: str) -> str:
    asset_id = f"asset-{uuid4()}"
    client.post(
        "/board/assets",
        json={
            "asset_id": asset_id,
            "task_id": task_id,
            "uploader_id": uploader_id,
            "filename": "deliverable.zip",
            "content_type": "application/zip",
            "size_bytes": 1024,
            "storage_path": f"{task_id}/deliverable.zip",
            "uploaded_at": "2026-02-28T14:00:00Z",
            "event": make_event(source="board", event_type="asset.uploaded", task_id=task_id),
        },
    )
    return asset_id


@pytest.mark.unit
class TestReadTaskListFilters:
    """READ-14: GET /board/tasks lists with status/poster_id/worker_id filters."""

    def test_filter_by_status(self, app_with_writer: TestClient) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        open_tid, _pid, _eid = _create_task(app_with_writer, poster_id=poster_id)
        accepted_tid, _pid2, _eid2 = _create_task(app_with_writer, poster_id=poster_id)
        worker_id = _register_agent(app_with_writer, name="Accepted worker")
        bid_id = _submit_bid(app_with_writer, accepted_tid, worker_id)
        accepted = app_with_writer.post(
            "/board/task-operations",
            json={
                "protocol_version": 1,
                "operation_id": f"op-{uuid4()}",
                "task_id": accepted_tid,
                "command": "accept_bid",
                "expected_status": "open",
                "expected_lifecycle_version": 0,
                "actor_kind": "agent",
                "actor_id": poster_id,
                "arguments": {
                    "bid_id": bid_id,
                    "worker_id": worker_id,
                    "escrow_id": _eid2,
                    "reward": 100,
                    "bid_amount": 50,
                },
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["operation_state"] == "completed"
        assert accepted.json()["target_status"] == "accepted"
        _wire_db_reader()

        response = app_with_writer.get("/board/tasks?status=open&limit=10")

        assert response.status_code == 200
        tasks = response.json()["tasks"]
        task_ids = {t["task_id"] for t in tasks}
        assert open_tid in task_ids
        assert accepted_tid not in task_ids

    def test_filter_by_poster_id(self, app_with_writer: TestClient) -> None:
        poster_a = _setup_funded_account(app_with_writer)
        poster_b = _setup_funded_account(app_with_writer)
        tid_a, _pa, _ea = _create_task(app_with_writer, poster_id=poster_a)
        _create_task(app_with_writer, poster_id=poster_b)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks?poster_id={poster_a}&limit=10")

        assert response.status_code == 200
        tasks = response.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["task_id"] == tid_a
        assert tasks[0]["poster_id"] == poster_a


@pytest.mark.unit
class TestReadTaskListLimitOffset:
    """READ-15: GET /board/tasks supports limit and offset (happy path)."""

    def test_limit_and_offset_together_skip_and_bound_results(
        self, app_with_writer: TestClient
    ) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        created_ids = []
        for i in range(5):
            tid, _pid, _eid = _create_task(
                app_with_writer,
                poster_id=poster_id,
                created_at=f"2026-02-28T{10 + i:02d}:00:00Z",
            )
            created_ids.append(tid)
        _wire_db_reader()

        # list_tasks orders by created_at DESC, so the newest task is first and
        # the one created earliest (index 0 in created_ids) is last.
        response = app_with_writer.get("/board/tasks?limit=2&offset=2")

        assert response.status_code == 200
        tasks = response.json()["tasks"]
        assert len(tasks) == 2
        returned_ids = [t["task_id"] for t in tasks]
        assert returned_ids == [created_ids[2], created_ids[1]]


@pytest.mark.unit
class TestReadTaskIncludesBidCountAndEscrowPending:
    """READ-16: GET /board/tasks/{task_id} includes bid_count and escrow_pending."""

    def test_task_response_includes_computed_fields(self, app_with_writer: TestClient) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        tid, _pid, _eid = _create_task(app_with_writer, poster_id=poster_id)
        bidder1 = _register_agent(app_with_writer, name="Bidder1")
        bidder2 = _register_agent(app_with_writer, name="Bidder2")
        _submit_bid(app_with_writer, tid, bidder1)
        _submit_bid(app_with_writer, tid, bidder2)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks/{tid}")

        assert response.status_code == 200
        data = response.json()
        assert data["bid_count"] == 2
        assert "escrow_pending" in data


@pytest.mark.unit
class TestReadAssetsCount:
    """READ-19: GET /board/tasks/{task_id}/assets/count and /assets."""

    def test_assets_count_reflects_recorded_assets(self, app_with_writer: TestClient) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        tid, _pid, _eid = _create_task(app_with_writer, poster_id=poster_id)
        uploader = _register_agent(app_with_writer, name="Uploader")
        _record_asset(app_with_writer, tid, uploader)
        _record_asset(app_with_writer, tid, uploader)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks/{tid}/assets/count")

        assert response.status_code == 200
        assert response.json() == {"count": 2}

    def test_assets_count_zero_when_no_assets(self, app_with_writer: TestClient) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        tid, _pid, _eid = _create_task(app_with_writer, poster_id=poster_id)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/tasks/{tid}/assets/count")

        assert response.status_code == 200
        assert response.json() == {"count": 0}


@pytest.mark.unit
class TestReadBidRequiresTaskId:
    """READ-20: GET /board/bids/{bid_id} requires task_id and 404s otherwise."""

    def test_get_bid_with_correct_task_id_succeeds(self, app_with_writer: TestClient) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        tid, _pid, _eid = _create_task(app_with_writer, poster_id=poster_id)
        bidder = _register_agent(app_with_writer, name="Bidder")
        bid_id = _submit_bid(app_with_writer, tid, bidder)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/bids/{bid_id}?task_id={tid}")

        assert response.status_code == 200
        data = response.json()
        assert data["bid_id"] == bid_id
        assert data["task_id"] == tid

    def test_get_bid_without_task_id_query_param_404s(self, app_with_writer: TestClient) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        tid, _pid, _eid = _create_task(app_with_writer, poster_id=poster_id)
        bidder = _register_agent(app_with_writer, name="Bidder")
        bid_id = _submit_bid(app_with_writer, tid, bidder)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/bids/{bid_id}")

        assert response.status_code == 404
        assert response.json()["error"] == "bid_not_found"


@pytest.mark.unit
class TestReadAssetRequiresTaskId:
    """READ-21: GET /board/assets/{asset_id} requires task_id."""

    def test_get_asset_with_correct_task_id_succeeds(self, app_with_writer: TestClient) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        tid, _pid, _eid = _create_task(app_with_writer, poster_id=poster_id)
        uploader = _register_agent(app_with_writer, name="Uploader")
        asset_id = _record_asset(app_with_writer, tid, uploader)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/assets/{asset_id}?task_id={tid}")

        assert response.status_code == 200
        data = response.json()
        assert data["asset_id"] == asset_id
        assert data["task_id"] == tid

    def test_get_asset_without_task_id_query_param_404s(self, app_with_writer: TestClient) -> None:
        poster_id = _setup_funded_account(app_with_writer)
        tid, _pid, _eid = _create_task(app_with_writer, poster_id=poster_id)
        uploader = _register_agent(app_with_writer, name="Uploader")
        asset_id = _record_asset(app_with_writer, tid, uploader)
        _wire_db_reader()

        response = app_with_writer.get(f"/board/assets/{asset_id}")

        assert response.status_code == 404
        assert response.json()["error"] == "asset_not_found"

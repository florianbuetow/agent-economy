"""board_tasks.dispute_id: strict schema admission, writes, and reads (MP-06/GAP-B9)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.conftest import make_event
from tests.fakes.native_board_history import (
    BusinessClock,
    TaskGraph,
    accept_bid,
    finalize,
    operation_body,
    publish_task,
    submit_bid,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

BUSY_TIMEOUT_MS = 5000
JOURNAL_MODE = "wal"
_BUSINESS_START_US = parse_utc_text_to_epoch_us("2026-03-01T10:20:00Z")
_SUBMISSION_TIME_US = parse_utc_text_to_epoch_us("2026-03-01T10:21:00Z")
_DISPUTE_TIME_US = parse_utc_text_to_epoch_us("2026-03-01T10:22:00Z")
_CLAIM_TIME_US = parse_utc_text_to_epoch_us("2026-03-01T10:23:00Z")
_DISPUTE_ID = "disp-1"
_DISPUTE_REASON = "Spec not met"
_REBUTTAL_DEADLINE = "2026-03-01T11:23:00Z"


@pytest.fixture(autouse=True)
def _fixed_gateway_business_clock(monkeypatch: pytest.MonkeyPatch) -> BusinessClock:
    """Control lifecycle time without changing JWT validation time."""
    clock = BusinessClock(now_us=_BUSINESS_START_US)
    monkeypatch.setattr("db_gateway_service.task_time.utc_now_us", clock)
    return clock


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _board_task_columns(db_path: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    rows = conn.execute("PRAGMA table_info(board_tasks)").fetchall()
    conn.close()
    return [str(row[1]) for row in rows]


def _create_old_shape_board_tasks(db_path: str) -> None:
    """Create a board_tasks table predating the dispute_id column."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE board_tasks ("
        "task_id TEXT PRIMARY KEY, "
        "poster_id TEXT NOT NULL, "
        "title TEXT NOT NULL, "
        "spec TEXT NOT NULL, "
        "reward INTEGER NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'open', "
        "dispute_reason TEXT, "
        "created_at TEXT NOT NULL"
        ")"
    )
    conn.commit()
    conn.close()


def _init_writer(db_path: str, schema_sql: str) -> None:
    writer = DbWriter(
        db_path=db_path,
        busy_timeout_ms=BUSY_TIMEOUT_MS,
        journal_mode=JOURNAL_MODE,
        schema_sql=schema_sql,
        current_task_board_caller_kid="gateway-task-board",
    )
    writer.close()


def _read_one(db_writer: DbWriter, query: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
    conn = sqlite3.connect(db_writer._db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(query, params).fetchone()
    conn.close()
    return row


def _register_agent(client: TestClient, name: str = "Agent") -> str:
    agent_id = f"a-{uuid4()}"
    response = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-03-01T10:00:00Z",
            "event": make_event(),
        },
    )
    assert response.status_code == 201
    return agent_id


def _create_funded_account(client: TestClient, *, name: str, balance: int) -> str:
    agent_id = _register_agent(client, name=name)
    response = client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid4()}",
            "account_id": agent_id,
            "balance": balance,
            "created_at": "2026-03-01T10:05:00Z",
            "initial_credit": {
                "tx_id": f"tx-{uuid4()}",
                "amount": balance,
                "reference": "initial_balance",
                "timestamp": "2026-03-01T10:05:00Z",
            },
            "event": make_event(source="bank", event_type="account.created"),
        },
    )
    assert response.status_code == 201
    return agent_id


def _create_task(client: TestClient, *, poster_id: str) -> TaskGraph:
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    publish_task(
        client,
        task_id=task_id,
        poster_id=poster_id,
        escrow_id=escrow_id,
        title="Dispute id task",
        spec="Validate dispute_id column",
        reward=100,
        bidding_deadline_seconds=3600,
        acceptance_window_seconds=3600,
        execution_deadline_seconds=7200,
        review_deadline_seconds=1800,
        lock_timestamp="2026-03-01T10:10:00Z",
    )
    return TaskGraph(task_id=task_id, poster_id=poster_id, escrow_id=escrow_id)


def _prepare_disputed_task(
    client: TestClient,
    poster_id: str,
    clock: BusinessClock,
) -> TaskGraph:
    """Persist a real native task history ending in the configured dispute ID."""
    worker_id = _register_agent(client, name="Worker")
    graph = _create_task(client, poster_id=poster_id)
    bid_id, bid = submit_bid(client, graph, worker_id)
    assert bid.status_code == 200
    accepted = accept_bid(client, graph, worker_id, bid_id)
    assert accepted.status_code == 200

    clock.now_us = _SUBMISSION_TIME_US
    asset = client.post(
        "/board/assets",
        json={
            "admission": {
                "expected_status": "accepted",
                "expected_lifecycle_version": 1,
                "max_files_per_task": 10,
            },
            "asset_id": f"asset-{uuid4()}",
            "task_id": graph.task_id,
            "uploader_id": worker_id,
            "filename": "deliverable.txt",
            "content_type": "text/plain",
            "size_bytes": 7,
            "storage_path": f"assets/{graph.task_id}/deliverable.txt",
            "content_hash": "a" * 64,
            "uploaded_at": "2026-03-01T10:21:00Z",
            "event": make_event(
                source="board",
                event_type="asset.uploaded",
                task_id=graph.task_id,
            ),
        },
    )
    assert asset.status_code == 201
    submitted = client.post(
        "/board/task-operations",
        json=operation_body(
            operation_id=f"op-{uuid4()}",
            task_id=graph.task_id,
            command="submit_deliverable",
            expected_status="accepted",
            expected_lifecycle_version=1,
            actor_kind="agent",
            actor_id=worker_id,
            arguments={"worker_id": worker_id},
        ),
    )
    assert submitted.status_code == 200

    clock.now_us = _DISPUTE_TIME_US
    dispute_operation_id = f"op-{uuid4()}"
    pending = client.post(
        "/board/task-operations",
        json=operation_body(
            operation_id=dispute_operation_id,
            task_id=graph.task_id,
            command="dispute_task",
            expected_status="submitted",
            expected_lifecycle_version=2,
            actor_kind="agent",
            actor_id=poster_id,
            arguments={
                "poster_id": poster_id,
                "worker_id": worker_id,
                "escrow_id": graph.escrow_id,
                "reward": 100,
                "reason": _DISPUTE_REASON,
            },
        ),
    )
    assert pending.status_code == 202

    clock.now_us = _CLAIM_TIME_US
    claim = client.post(
        "/court/claims",
        json={
            "claim_id": _DISPUTE_ID,
            "task_id": graph.task_id,
            "claimant_id": poster_id,
            "respondent_id": worker_id,
            "reason": _DISPUTE_REASON,
            "status": "rebuttal_pending",
            "rebuttal_deadline": _REBUTTAL_DEADLINE,
            "filed_at": "2026-03-01T10:23:00Z",
            "event": make_event(
                source="court",
                event_type="claim.filed",
                task_id=graph.task_id,
            ),
        },
    )
    assert claim.status_code == 201
    completed = finalize(client, dispute_operation_id)
    assert completed.status_code == 200
    return graph


@pytest.mark.unit
class TestDisputeIdSchemaAdmission:
    """Partial old tables are preserved; only complete current schemas start."""

    def test_partial_old_table_is_rejected_without_adding_dispute_id(
        self, tmp_db_path: str, schema_sql: str
    ) -> None:
        _create_old_shape_board_tasks(tmp_db_path)
        assert "dispute_id" not in _board_task_columns(tmp_db_path)
        before = Path(tmp_db_path).read_bytes()

        with pytest.raises(ServiceError, match="incomplete or unknown"):
            _init_writer(tmp_db_path, schema_sql)

        assert "dispute_id" not in _board_task_columns(tmp_db_path)
        assert Path(tmp_db_path).read_bytes() == before

    def test_repeated_partial_admission_does_not_modify_columns(
        self, tmp_db_path: str, schema_sql: str
    ) -> None:
        _create_old_shape_board_tasks(tmp_db_path)
        before = Path(tmp_db_path).read_bytes()
        columns = _board_task_columns(tmp_db_path)

        for _attempt in range(2):
            with pytest.raises(ServiceError, match="incomplete or unknown"):
                _init_writer(tmp_db_path, schema_sql)

        assert _board_task_columns(tmp_db_path) == columns
        assert Path(tmp_db_path).read_bytes() == before

    def test_fresh_database_has_dispute_id(self, tmp_db_path: str, schema_sql: str) -> None:
        _init_writer(tmp_db_path, schema_sql)

        assert "dispute_id" in _board_task_columns(tmp_db_path)


@pytest.mark.unit
class TestDisputeIdWrites:
    """The gateway accepts dispute_id on status updates and returns it on reads."""

    def test_native_dispute_sets_dispute_id(
        self,
        app_with_writer: TestClient,
        db_writer: DbWriter,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        graph = _prepare_disputed_task(
            app_with_writer,
            poster_id,
            _fixed_gateway_business_clock,
        )

        row = _read_one(
            db_writer,
            "SELECT dispute_id FROM board_tasks WHERE task_id = ?",
            (graph.task_id,),
        )
        assert row is not None
        assert row["dispute_id"] == _DISPUTE_ID

    def test_task_read_returns_native_dispute_id(
        self,
        app_with_writer: TestClient,
        _fixed_gateway_business_clock: BusinessClock,
    ) -> None:
        _wire_db_reader()
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        graph = _prepare_disputed_task(
            app_with_writer,
            poster_id,
            _fixed_gateway_business_clock,
        )

        response = app_with_writer.get(f"/board/tasks/{graph.task_id}")

        assert response.status_code == 200
        assert response.json()["dispute_id"] == _DISPUTE_ID

    def test_create_task_leaves_dispute_id_null(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        graph = _create_task(app_with_writer, poster_id=poster_id)

        row = _read_one(
            db_writer,
            "SELECT dispute_id FROM board_tasks WHERE task_id = ?",
            (graph.task_id,),
        )
        assert row is not None
        assert row["dispute_id"] is None

"""board_tasks.dispute_id column: migration, writes, and reads (GAP-B9)."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

BUSY_TIMEOUT_MS = 5000
JOURNAL_MODE = "wal"


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


def _create_task(client: TestClient, *, poster_id: str) -> str:
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    lock_response = client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": escrow_id,
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": "2026-03-01T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=task_id),
        },
    )
    assert lock_response.status_code == 201

    task_response = client.post(
        "/board/tasks",
        json={
            "task_id": task_id,
            "poster_id": poster_id,
            "title": "Dispute id task",
            "spec": "Validate dispute_id column",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 7200,
            "review_deadline_seconds": 1800,
            "bidding_deadline": "2026-03-01T11:00:00Z",
            "escrow_id": escrow_id,
            "created_at": "2026-03-01T10:12:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=task_id),
        },
    )
    assert task_response.status_code == 201
    return task_id


@pytest.mark.unit
class TestDisputeIdMigration:
    """An existing database must gain board_tasks.dispute_id on startup."""

    def test_migration_adds_dispute_id_to_existing_database(
        self, tmp_db_path: str, schema_sql: str
    ) -> None:
        _create_old_shape_board_tasks(tmp_db_path)
        assert "dispute_id" not in _board_task_columns(tmp_db_path)

        _init_writer(tmp_db_path, schema_sql)

        assert "dispute_id" in _board_task_columns(tmp_db_path)

    def test_migration_is_idempotent(self, tmp_db_path: str, schema_sql: str) -> None:
        _create_old_shape_board_tasks(tmp_db_path)

        _init_writer(tmp_db_path, schema_sql)
        _init_writer(tmp_db_path, schema_sql)

        assert _board_task_columns(tmp_db_path).count("dispute_id") == 1

    def test_fresh_database_has_dispute_id(self, tmp_db_path: str, schema_sql: str) -> None:
        _init_writer(tmp_db_path, schema_sql)

        assert "dispute_id" in _board_task_columns(tmp_db_path)


@pytest.mark.unit
class TestDisputeIdWrites:
    """The gateway accepts dispute_id on status updates and returns it on reads."""

    def test_status_update_sets_dispute_id(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id = _create_task(app_with_writer, poster_id=poster_id)

        response = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {
                    "status": "disputed",
                    "disputed_at": "2026-03-01T12:00:00Z",
                    "dispute_reason": "Spec not met",
                    "dispute_id": "disp-1",
                },
                "event": make_event(source="board", event_type="task.disputed", task_id=task_id),
            },
        )
        assert response.status_code == 200

        row = _read_one(
            db_writer,
            "SELECT dispute_id FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        assert row is not None
        assert row["dispute_id"] == "disp-1"

    def test_task_read_returns_dispute_id(self, app_with_writer: TestClient) -> None:
        _wire_db_reader()
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id = _create_task(app_with_writer, poster_id=poster_id)

        update = app_with_writer.post(
            f"/board/tasks/{task_id}/status",
            json={
                "updates": {"status": "disputed", "dispute_id": "disp-1"},
                "event": make_event(source="board", event_type="task.disputed", task_id=task_id),
            },
        )
        assert update.status_code == 200

        response = app_with_writer.get(f"/board/tasks/{task_id}")

        assert response.status_code == 200
        assert response.json()["dispute_id"] == "disp-1"

    def test_create_task_leaves_dispute_id_null(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        poster_id = _create_funded_account(app_with_writer, name="Poster", balance=500)
        task_id = _create_task(app_with_writer, poster_id=poster_id)

        row = _read_one(
            db_writer,
            "SELECT dispute_id FROM board_tasks WHERE task_id = ?",
            (task_id,),
        )
        assert row is not None
        assert row["dispute_id"] is None

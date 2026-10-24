"""MP-05 baseline: publication must not advertise incoherent escrow backing.

All setup effects use signed Gateway routes and the real financial writer. The
old publication route must be retired; equivalent negative cases must also be
exercised through the native publication finalizer when that interface exists.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from service_auth.financial_identity import operation_id_for

from tests.fakes.native_board_history import register_agent

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter

pytestmark = pytest.mark.unit

_TIME = "2099-01-01T12:00:00Z"


def _event(kind: str, agent_id: str, task_id: str | None) -> dict[str, object]:
    return {
        "event_source": "central-bank",
        "event_type": kind,
        "timestamp": _TIME,
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": "MP-05 financial binding fixture",
        "payload": "{}",
    }


def _poster(client: TestClient) -> str:
    key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    agent_id = register_agent(
        client,
        name="MP05 financial binding poster",
        public_key="ed25519:" + base64.b64encode(key).decode("ascii"),
    )
    response = client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid4()}",
            "account_id": agent_id,
            "balance": 500,
            "created_at": _TIME,
            "initial_credit": {
                "tx_id": f"tx-{uuid4()}",
                "amount": 500,
                "reference": "initial_balance",
                "timestamp": _TIME,
            },
            "event": _event("account.created", agent_id, None),
        },
    )
    assert response.status_code == 201, response.text
    return agent_id


@pytest.mark.parametrize("mismatch", ["payer", "task", "amount", "resolved"])
def test_unproven_escrow_cannot_back_a_public_task(
    app_with_writer: TestClient,
    db_writer: DbWriter,
    mismatch: str,
) -> None:
    client = app_with_writer
    payer_id = _poster(client)
    task_id = f"t-{uuid4()}"
    escrow_id = f"esc-{uuid4()}"
    lock_id = operation_id_for("task-lock", (task_id, payer_id))
    locked = client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": lock_id,
            "escrow_id": escrow_id,
            "payer_account_id": payer_id,
            "amount": 100,
            "task_id": task_id,
            "created_at": _TIME,
            "tx_id": f"tx-{uuid4()}",
            "event": _event("escrow.locked", payer_id, task_id),
        },
    )
    assert locked.status_code == 201, locked.text
    assert locked.json()["receipt"]["operation_id"] == lock_id
    assert locked.json()["current_escrow"]["status"] == "locked"

    poster_id = _poster(client) if mismatch == "payer" else payer_id
    published_task_id = f"t-{uuid4()}" if mismatch == "task" else task_id
    if mismatch == "resolved":
        released = client.post(
            "/bank/escrow/release",
            json={
                "operation_id": f"op-{uuid4()}",
                "escrow_id": escrow_id,
                "recipient_account_id": payer_id,
                "source_reference": f"task:{task_id}:posting_compensation",
                "tx_id": f"tx-{uuid4()}",
                "resolved_at": _TIME,
                "event": _event("escrow.released", payer_id, task_id),
            },
        )
        assert released.status_code == 200, released.text
        assert released.json()["current_escrow"]["status"] == "released"

    body: dict[str, Any] = {
        "task_id": published_task_id,
        "poster_id": poster_id,
        "title": "Require matching locked funds",
        "spec": "This task must only be advertised with its own secured reward.",
        "reward": 101 if mismatch == "amount" else 100,
        "status": "open",
        "bidding_deadline_seconds": 3600,
        "acceptance_window_seconds": 1800,
        "deadline_seconds": 7200,
        "review_deadline_seconds": 900,
        "bidding_deadline": "2099-01-01T13:00:00Z",
        "escrow_id": escrow_id,
        "created_at": _TIME,
        "event": {
            **_event("task.created", poster_id, published_task_id),
            "event_source": "task-board",
        },
    }
    before = tuple(db_writer.connection.iterdump())
    response = client.post("/board/tasks", json=body)
    advertised = db_writer.connection.execute(
        "SELECT task_id,poster_id,reward,escrow_id,status FROM board_tasks"
    ).fetchall()
    diagnostic = {
        "mismatch": mismatch,
        "response_status": response.status_code,
        "response": response.json(),
        "advertised": [tuple(row) for row in advertised],
    }
    assert response.status_code == 409, diagnostic
    assert tuple(db_writer.connection.iterdump()) == before, diagnostic

"""Router fixtures must retain real native state and their declared signing authority."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from tests.fakes.gateway_task_store import GatewayTaskStore
from tests.helpers import generate_keypair, make_jws_token
from tests.unit.routers.conftest import (
    approve_task,
    file_dispute,
    new_operation_fields,
    setup_task_in_dispute,
    setup_task_in_review,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = pytest.mark.unit


def _store() -> GatewayTaskStore:
    store = get_app_state().store
    assert isinstance(store, GatewayTaskStore)
    return store


async def test_router_approval_has_the_matching_persisted_receipt(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    task_id = await setup_task_in_review(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    response = await approve_task(client, alice_keypair, alice_agent_id, task_id)
    assert response.status_code == 200, response.text
    operation_id = response.json()["operation"]["operation_id"]
    rows = (
        _store()
        .writer.connection.execute(
            "SELECT r.operation_id,r.operation_kind,l.account_id,l.amount,o.operation_state "
            "FROM bank_operation_receipts AS r JOIN bank_operation_legs AS l "
            "ON l.operation_id=r.operation_id JOIN board_task_operations AS o "
            "ON o.operation_id=r.operation_id WHERE r.operation_id=?",
            (operation_id,),
        )
        .fetchall()
    )
    assert [tuple(row) for row in rows] == [
        (operation_id, "escrow_release", bob_agent_id, 100, "completed")
    ]


async def test_success_body_without_claim_cannot_complete_router_dispute(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    task_id = await setup_task_in_review(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    state = get_app_state()
    assert state.platform_agent is not None
    monkeypatch.setattr(
        state.platform_agent,
        "file_claim",
        AsyncMock(
            return_value={"dispute_id": "clm-no-persisted-fact", "status": "rebuttal_pending"}
        ),
    )
    response = await file_dispute(client, alice_keypair, alice_agent_id, task_id)
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "submitted"
    assert response.json()["operation"]["operation_state"] == "claimed"
    assert (
        _store()
        .writer.connection.execute("SELECT COUNT(*) FROM court_claims WHERE task_id=?", (task_id,))
        .fetchone()[0]
        == 0
    )


async def test_router_rebuttal_uses_the_actual_atomic_court_timestamp(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    store = _store()
    task = store.get_task(task_id)
    assert task is not None
    payload: dict[str, Any] = {
        "action": "submit_rebuttal",
        "task_id": task_id,
        "dispute_id": task["dispute_id"],
        "worker_id": bob_agent_id,
        "rebuttal": "The delivery matches the specification.",
    }
    response = await client.post(
        f"/tasks/{task_id}/rebuttal",
        json={"token": make_jws_token(bob_keypair[0], bob_agent_id, payload)},
    )
    assert response.status_code == 200, response.text
    rows = store.writer.connection.execute(
        "SELECT r.submitted_at,t.rebuttal_submitted_at,c.lifecycle_version "
        "FROM court_rebuttals AS r JOIN board_tasks AS t ON t.dispute_id=r.claim_id "
        "JOIN board_task_contracts AS c ON c.task_id=t.task_id WHERE t.task_id=?",
        (task_id,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == rows[0][1] and rows[0][0] is not None
    assert rows[0][2] == 3
    assert (
        store.writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND event_type='rebuttal.submitted'",
            (task_id,),
        ).fetchone()[0]
        == 1
    )


async def test_router_fixture_rejects_an_impostor_key_under_platform_identity(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    platform_agent_id: str,
) -> None:
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    store = _store()
    task = store.get_task(task_id)
    assert task is not None
    before = tuple(store.writer.connection.iterdump())
    impostor_private_key, _ = generate_keypair()
    payload = {
        "action": "record_ruling",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "ruling_id": task["dispute_id"],
        "worker_pct": 100,
        "ruling_summary": "Untrusted ruling must not settle escrow.",
    }
    response = await client.post(
        f"/tasks/{task_id}/ruling",
        json={"token": make_jws_token(impostor_private_key, platform_agent_id, payload)},
    )
    assert response.status_code == 403, response.text
    assert tuple(store.writer.connection.iterdump()) == before

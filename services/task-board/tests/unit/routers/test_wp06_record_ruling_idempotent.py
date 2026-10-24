"""Native ruling replay keeps one operation, settlement, and lifecycle event."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import new_operation_fields, setup_task_in_dispute
from tests.unit.routers.test_disputes import fixture_bank, stored_dispute_id

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response

    from tests.fakes.gateway_task_store import GatewayTaskStore


def _ruling_payload(task_id: str) -> dict[str, Any]:
    return {
        "action": "record_ruling",
        **new_operation_fields(task_id),
        "task_id": task_id,
        "ruling_id": stored_dispute_id(task_id),
        "worker_pct": 50,
        "ruling_summary": "Split ruling",
    }


async def _record_ruling(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    platform_id: str,
    payload: dict[str, Any],
) -> Response:
    token = make_jws_token(platform_keypair[0], platform_id, payload)
    return await client.post(f"/tasks/{payload['task_id']}/ruling", json={"token": token})


def _snapshot(store: GatewayTaskStore, task_id: str, escrow_id: str) -> tuple[Any, ...]:
    connection = store.writer.connection
    return (
        tuple(
            connection.execute("SELECT * FROM board_tasks WHERE task_id=?", (task_id,)).fetchone()
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM board_task_operations WHERE task_id=? ORDER BY operation_id",
                (task_id,),
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM bank_operation_receipts WHERE escrow_id=? ORDER BY operation_id",
                (escrow_id,),
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM bank_transactions WHERE reference=? ORDER BY tx_id", (escrow_id,)
            )
        ),
        tuple(
            tuple(row)
            for row in connection.execute("SELECT * FROM bank_accounts ORDER BY account_id")
        ),
        tuple(
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM events WHERE task_id=? ORDER BY event_id", (task_id,)
            )
        ),
    )


@pytest.mark.unit
async def test_identical_ruling_id_rerecord_is_idempotent(
    app: Any,
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
    platform_keypair: Any,
    platform_agent_id: str,
) -> None:
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    payload = _ruling_payload(task_id)
    split_mock = fixture_bank().escrow_split
    first = await _record_ruling(client, platform_keypair, platform_agent_id, payload)
    assert first.status_code == 200
    first_body = first.json()
    assert first_body["status"] == "ruled"
    assert split_mock.await_count == 1
    store = app.state.gateway_store
    before_replay = _snapshot(store, task_id, first_body["escrow_id"])

    # Retain the original business payload, including version and operation ID.
    second = await _record_ruling(client, platform_keypair, platform_agent_id, payload)
    assert second.status_code == 200
    assert second.json()["status"] == "ruled"
    assert second.json()["ruling_id"] == payload["ruling_id"]
    assert second.json()["operation"] == first_body["operation"]
    assert split_mock.await_count == 1
    assert _snapshot(store, task_id, first_body["escrow_id"]) == before_replay
    payouts = store.writer.connection.execute(
        "SELECT account_id,amount FROM bank_transactions "
        "WHERE reference=? AND type='escrow_release' ORDER BY account_id",
        (first_body["escrow_id"],),
    ).fetchall()
    assert [tuple(row) for row in payouts] == sorted([(alice_agent_id, 50), (bob_agent_id, 50)])
    assert (
        store.writer.connection.execute(
            "SELECT COUNT(*) FROM bank_operation_receipts "
            "WHERE escrow_id=? AND operation_kind='escrow_split'",
            (first_body["escrow_id"],),
        ).fetchone()[0]
        == 1
    )
    assert (
        store.writer.connection.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND event_type='task.ruled'", (task_id,)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.unit
async def test_different_ruling_id_on_ruled_task_conflicts(
    app: Any,
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
    platform_keypair: Any,
    platform_agent_id: str,
) -> None:
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    first = await _record_ruling(
        client, platform_keypair, platform_agent_id, _ruling_payload(task_id)
    )
    assert first.status_code == 200
    escrow_id = first.json()["escrow_id"]
    before = _snapshot(app.state.gateway_store, task_id, escrow_id)
    changed = _ruling_payload(task_id)
    changed["ruling_id"] = "clm-other-ruling"
    second = await _record_ruling(client, platform_keypair, platform_agent_id, changed)
    assert second.status_code == 409
    assert second.json()["error"] == "invalid_status"
    assert _snapshot(app.state.gateway_store, task_id, escrow_id) == before
    fixture_bank().escrow_split.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("worker_pct", 0),
        ("worker_pct", 49),
        ("worker_pct", 100),
        ("ruling_summary", "Changed verdict"),
        ("ruling_id", "clm-changed-ruling"),
    ],
)
async def test_changed_ruling_terms_cannot_reuse_completed_operation(
    app: Any,
    client: AsyncClient,
    alice_keypair: Any,
    alice_agent_id: str,
    bob_keypair: Any,
    bob_agent_id: str,
    platform_keypair: Any,
    platform_agent_id: str,
    field: str,
    replacement: str | int,
) -> None:
    task_id = await setup_task_in_dispute(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    payload = _ruling_payload(task_id)
    first = await _record_ruling(client, platform_keypair, platform_agent_id, payload)
    assert first.status_code == 200
    escrow_id = first.json()["escrow_id"]
    before = _snapshot(app.state.gateway_store, task_id, escrow_id)
    changed = dict(payload)
    changed[field] = replacement
    response = await _record_ruling(client, platform_keypair, platform_agent_id, changed)
    assert response.status_code == 409
    assert response.json()["error"] == "operation_id_conflict"
    assert _snapshot(app.state.gateway_store, task_id, escrow_id) == before
    fixture_bank().escrow_split.assert_awaited_once()

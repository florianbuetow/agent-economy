"""Public MP-07 submission, approval, and cancellation command proofs."""

from __future__ import annotations

import importlib
import sqlite3
import uuid
from typing import TYPE_CHECKING, Any, Protocol, cast
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from fastapi import FastAPI
    from httpx import AsyncClient, Response

    from task_board_service.services.task_db_client import TaskDbClient

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _TaskSetup(Protocol):
    task_id: str
    poster_id: str
    worker_id: str


class _BidSetup(Protocol):
    task: _TaskSetup
    bid_id: str
    amount: int
    operation_id: str


_replay_helpers = importlib.import_module("tests.integration.test_mp07_public_command_replay")
_create_open_task = cast(
    "Callable[[AsyncClient, AsyncClient, Ed25519PrivateKey], Awaitable[_TaskSetup]]",
    _replay_helpers.__dict__["_create_open_task"],
)
_create_bid = cast(
    "Callable[..., Awaitable[_BidSetup]]",
    _replay_helpers.__dict__["_create_bid"],
)
_accept_payload = cast(
    "Callable[..., dict[str, Any]]",
    _replay_helpers.__dict__["_accept_payload"],
)
_post_signed = cast(
    "Callable[..., Awaitable[Response]]",
    _replay_helpers.__dict__["_post_signed"],
)
_post_with_discarded_success = cast(
    "Callable[..., Awaitable[Response]]",
    _replay_helpers.__dict__["_post_with_discarded_success"],
)


def _rows(
    db_path: str,
    query: str,
    parameters: tuple[object, ...] = (),
) -> tuple[tuple[object, ...], ...]:
    connection = sqlite3.connect(db_path)
    try:
        return tuple(tuple(row) for row in connection.execute(query, parameters).fetchall())
    finally:
        connection.close()


async def _create_accepted_task(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_key: Ed25519PrivateKey,
    worker_key: Ed25519PrivateKey,
) -> tuple[_TaskSetup, _BidSetup, dict[str, Any]]:
    setup = await _create_open_task(client, gateway_client, poster_key)
    bid = await _create_bid(
        client,
        worker_key,
        setup,
        operation_id=f"op-{uuid.uuid4()}",
        amount=90,
    )
    response = await _post_signed(
        client,
        path=f"/tasks/{setup.task_id}/bids/{bid.bid_id}/accept",
        private_key=poster_key,
        signer_id=setup.poster_id,
        payload=_accept_payload(
            bid,
            operation_id=f"op-{uuid.uuid4()}",
            expected_lifecycle_version=0,
        ),
    )
    assert response.status_code == 200, response.text
    accepted = response.json()
    assert accepted["status"] == "accepted"
    assert accepted["lifecycle_version"] == 1
    return setup, bid, accepted


def _insert_owned_asset(setup: _TaskSetup) -> None:
    state = get_app_state()
    store = cast("TaskDbClient", state.store)
    store.insert_asset(
        {
            "asset_id": f"asset-{uuid.uuid4()}",
            "task_id": setup.task_id,
            "uploader_id": setup.worker_id,
            "filename": "completed-work.txt",
            "content_type": "text/plain",
            "size_bytes": 14,
            "storage_path": f"assets/{setup.task_id}/completed-work.txt",
            "content_hash": "c" * 64,
            "uploaded_at": "2026-10-17T09:00:05Z",
        },
        expected_lifecycle_version=1,
        max_files_per_task=10,
    )


def _native_submit(setup: _TaskSetup, operation_id: str) -> dict[str, Any]:
    state = get_app_state()
    store = cast("TaskDbClient", state.store)
    return store.claim_task_operation(
        {
            "protocol_version": 1,
            "operation_id": operation_id,
            "task_id": setup.task_id,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": setup.worker_id,
            "arguments": {"worker_id": setup.worker_id},
        }
    )


async def _create_zero_balance_account(
    gateway_client: AsyncClient,
    *,
    account_id: str,
) -> None:
    response = await gateway_client.post(
        "/bank/accounts",
        json={
            "account_id": account_id,
            "balance": 0,
            "created_at": "2026-10-17T09:00:02Z",
            "event": {
                "event_source": "central-bank",
                "event_type": "account.created",
                "timestamp": "2026-10-17T09:00:02Z",
                "agent_id": account_id,
                "summary": f"Created account for {account_id}",
                "payload": "{}",
            },
        },
    )
    assert response.status_code == 201, response.text


def _install_persisting_release(
    gateway_client: AsyncClient,
    *,
    db_path: str,
    task_id: str,
    command: str,
    operation_id: str,
    source_status: str,
    source_version: int,
    escrow_id: str,
    recipient_id: str,
    source_reference: str,
) -> AsyncMock:
    async def _release(*args: object, **kwargs: object) -> dict[str, Any]:
        assert args == ()
        assert kwargs == {
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_id,
            "operation_id": operation_id,
            "source_reference": source_reference,
        }
        assert _rows(
            db_path,
            "SELECT command, source_status, source_lifecycle_version, operation_state, "
            "effect_state, effect_operation_id FROM board_task_operations "
            "WHERE operation_id=?",
            (operation_id,),
        ) == ((command, source_status, source_version, "claimed", "pending", operation_id),)
        assert _rows(
            db_path,
            "SELECT t.status, c.lifecycle_version FROM board_tasks AS t "
            "JOIN board_task_contracts AS c ON c.task_id=t.task_id WHERE t.task_id=?",
            (task_id,),
        ) == ((source_status, source_version),)
        response = await gateway_client.post(
            "/bank/escrow/release",
            json={
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_id,
                "source_reference": source_reference,
                "tx_id": f"tx-{uuid.uuid4()}",
                "resolved_at": "2026-10-17T09:00:10Z",
                "event": {
                    "event_source": "central-bank",
                    "event_type": "escrow.released",
                    "timestamp": "2026-10-17T09:00:10Z",
                    "task_id": task_id,
                    "agent_id": recipient_id,
                    "summary": f"Released escrow for {task_id}",
                    "payload": "{}",
                },
            },
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert isinstance(result, dict)
        assert result["outcome"] == "native_receipt"
        return result

    release = AsyncMock(side_effect=_release)
    state = get_app_state()
    assert state.central_bank_client is not None
    state.central_bank_client.escrow_release = release
    return release


def _operation_rows(
    db_path: str,
    task_id: str,
) -> tuple[tuple[object, ...], ...]:
    return _rows(
        db_path,
        "SELECT operation_id, command, operation_state, effect_state, event_id, result_json "
        "FROM board_task_operations WHERE task_id=? ORDER BY operation_id",
        (task_id,),
    )


def _lifecycle_events(
    db_path: str,
    task_id: str,
    event_type: str,
) -> tuple[tuple[object, ...], ...]:
    return _rows(
        db_path,
        "SELECT event_id, event_type FROM events WHERE task_id=? AND event_source='board' "
        "AND event_type=? ORDER BY event_id",
        (task_id, event_type),
    )


async def test_public_submission_replays_original_native_result_after_response_loss(
    app: FastAPI,
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, _bid, _accepted = await _create_accepted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    _insert_owned_asset(setup)
    operation_id = f"op-{uuid.uuid4()}"
    payload = {
        "action": "submit_deliverable",
        "operation_id": operation_id,
        "expected_lifecycle_version": 1,
        "task_id": setup.task_id,
        "worker_id": setup.worker_id,
    }
    path = f"/tasks/{setup.task_id}/submit"
    retained = await _post_with_discarded_success(
        app,
        path=path,
        private_key=worker_keypair[0],
        signer_id=setup.worker_id,
        payload=payload,
    )
    original = retained.json()
    replay = await _post_signed(
        client,
        path=path,
        private_key=worker_keypair[0],
        signer_id=setup.worker_id,
        payload=payload,
    )
    replayed = replay.json()
    operations = _operation_rows(gateway_db_path, setup.task_id)
    events = _lifecycle_events(gateway_db_path, setup.task_id, "task.submitted")

    assert replay.status_code == 200, replay.text
    assert original["status"] == replayed["status"] == "submitted"
    assert original["lifecycle_version"] == replayed["lifecycle_version"] == 2
    assert original["operation"] == replayed["operation"]
    operation = replayed["operation"]
    assert operation["operation_id"] == operation_id
    assert operation["command"] == "submit_deliverable"
    assert operation["operation_state"] == "completed"
    assert operation["effect_kind"] == operation["effect_state"] == "none"
    assert operation["result"] == {
        "task_id": setup.task_id,
        "command": "submit_deliverable",
        "status": "submitted",
        "lifecycle_version": 2,
        "event_id": operation["event_id"],
        "worker_id": setup.worker_id,
    }
    assert len([row for row in operations if row[0] == operation_id]) == 1
    assert len(events) == 1


async def test_public_approval_claims_before_bank_and_replays_one_fixed_reward_receipt(
    app: FastAPI,
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup, bid, accepted = await _create_accepted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    _insert_owned_asset(setup)
    submit = _native_submit(setup, f"op-{uuid.uuid4()}")
    assert submit["result"]["status"] == "submitted"
    await _create_zero_balance_account(gateway_client, account_id=setup.worker_id)

    operation_id = f"op-{uuid.uuid4()}"
    source_reference = f"task:{setup.task_id}:approved"
    release = _install_persisting_release(
        gateway_client,
        db_path=gateway_db_path,
        task_id=setup.task_id,
        command="approve_task",
        operation_id=operation_id,
        source_status="submitted",
        source_version=2,
        escrow_id=str(accepted["escrow_id"]),
        recipient_id=setup.worker_id,
        source_reference=source_reference,
    )
    payload = {
        "action": "approve_task",
        "operation_id": operation_id,
        "expected_lifecycle_version": 2,
        "task_id": setup.task_id,
        "poster_id": setup.poster_id,
    }
    path = f"/tasks/{setup.task_id}/approve"
    retained = await _post_with_discarded_success(
        app,
        path=path,
        private_key=poster_keypair[0],
        signer_id=setup.poster_id,
        payload=payload,
    )
    original = retained.json()
    before_replay_operations = _operation_rows(gateway_db_path, setup.task_id)
    before_replay_events = _lifecycle_events(gateway_db_path, setup.task_id, "task.approved")
    replay = await _post_signed(
        client,
        path=path,
        private_key=poster_keypair[0],
        signer_id=setup.poster_id,
        payload=payload,
    )

    assert replay.status_code == 200, replay.text
    assert replay.json() == original
    assert original["status"] == "approved"
    assert original["lifecycle_version"] == 3
    operation = original["operation"]
    assert operation["operation_id"] == operation_id
    assert operation["operation_state"] == "completed"
    assert operation["effect_kind"] == "bank_release"
    assert operation["effect_state"] == "succeeded"
    assert operation["effect_result_reference"] == operation_id
    assert operation["result"]["reward"] == accepted["reward"] == 100
    assert bid.amount == 90
    assert release.await_count == 1
    assert _operation_rows(gateway_db_path, setup.task_id) == before_replay_operations
    assert (
        _lifecycle_events(gateway_db_path, setup.task_id, "task.approved") == before_replay_events
    )
    assert len(before_replay_events) == 1
    assert _rows(
        gateway_db_path,
        "SELECT r.operation_id, r.operation_kind, r.source_reference, l.account_id, l.amount "
        "FROM bank_operation_receipts AS r JOIN bank_operation_legs AS l "
        "ON l.operation_id=r.operation_id WHERE r.operation_id=?",
        (operation_id,),
    ) == ((operation_id, "escrow_release", source_reference, setup.worker_id, 100),)


@pytest.mark.parametrize(
    ("case", "expected_error"),
    (("changed_replay", "operation_id_conflict"), ("stale_version", "task_version_conflict")),
)
async def test_public_approval_conflicts_before_bank_delivery(
    case: str,
    expected_error: str,
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
) -> None:
    setup, _bid, accepted = await _create_accepted_task(
        client,
        gateway_client,
        poster_keypair[0],
        worker_keypair[0],
    )
    _insert_owned_asset(setup)
    _native_submit(setup, f"op-{uuid.uuid4()}")
    operation_id = f"op-{uuid.uuid4()}"
    if case == "changed_replay":
        state = get_app_state()
        store = cast("TaskDbClient", state.store)
        pending = store.claim_task_operation(
            {
                "protocol_version": 1,
                "operation_id": operation_id,
                "task_id": setup.task_id,
                "command": "approve_task",
                "expected_status": "submitted",
                "expected_lifecycle_version": 2,
                "actor_kind": "agent",
                "actor_id": setup.poster_id,
                "arguments": {
                    "poster_id": setup.poster_id,
                    "worker_id": setup.worker_id,
                    "escrow_id": str(accepted["escrow_id"]),
                    "reward": 100,
                    "source_reference": f"task:{setup.task_id}:approved",
                },
            }
        )
        assert pending["operation_state"] == "claimed"

    state = get_app_state()
    assert state.central_bank_client is not None
    release = AsyncMock(return_value={"status": "released"})
    state.central_bank_client.escrow_release = release
    response = await _post_signed(
        client,
        path=f"/tasks/{setup.task_id}/approve",
        private_key=poster_keypair[0],
        signer_id=setup.poster_id,
        payload={
            "action": "approve_task",
            "operation_id": operation_id,
            "expected_lifecycle_version": 1,
            "task_id": setup.task_id,
            "poster_id": setup.poster_id,
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"] == expected_error
    assert release.await_count == 0


async def test_public_cancellation_claims_before_refund_and_replays_one_receipt(
    app: FastAPI,
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup = await _create_open_task(client, gateway_client, poster_keypair[0])
    task_response = await client.get(f"/tasks/{setup.task_id}")
    assert task_response.status_code == 200, task_response.text
    task = task_response.json()
    operation_id = f"op-{uuid.uuid4()}"
    source_reference = f"task:{setup.task_id}:cancelled"
    release = _install_persisting_release(
        gateway_client,
        db_path=gateway_db_path,
        task_id=setup.task_id,
        command="cancel_task",
        operation_id=operation_id,
        source_status="open",
        source_version=0,
        escrow_id=str(task["escrow_id"]),
        recipient_id=setup.poster_id,
        source_reference=source_reference,
    )
    payload = {
        "action": "cancel_task",
        "operation_id": operation_id,
        "expected_lifecycle_version": 0,
        "task_id": setup.task_id,
        "poster_id": setup.poster_id,
    }
    path = f"/tasks/{setup.task_id}/cancel"
    retained = await _post_with_discarded_success(
        app,
        path=path,
        private_key=poster_keypair[0],
        signer_id=setup.poster_id,
        payload=payload,
    )
    original = retained.json()
    before_replay_operations = _operation_rows(gateway_db_path, setup.task_id)
    before_replay_events = _lifecycle_events(gateway_db_path, setup.task_id, "task.cancelled")
    replay = await _post_signed(
        client,
        path=path,
        private_key=poster_keypair[0],
        signer_id=setup.poster_id,
        payload=payload,
    )

    assert replay.status_code == 200, replay.text
    assert replay.json() == original
    assert original["status"] == "cancelled"
    assert original["lifecycle_version"] == 1
    operation = original["operation"]
    assert operation["operation_id"] == operation_id
    assert operation["operation_state"] == "completed"
    assert operation["effect_kind"] == "bank_release"
    assert operation["effect_state"] == "succeeded"
    assert operation["result"]["reward"] == 100
    assert release.await_count == 1
    assert _operation_rows(gateway_db_path, setup.task_id) == before_replay_operations
    assert (
        _lifecycle_events(gateway_db_path, setup.task_id, "task.cancelled") == before_replay_events
    )
    assert len(before_replay_events) == 1
    assert _rows(
        gateway_db_path,
        "SELECT r.operation_id, r.operation_kind, r.source_reference, l.account_id, l.amount "
        "FROM bank_operation_receipts AS r JOIN bank_operation_legs AS l "
        "ON l.operation_id=r.operation_id WHERE r.operation_id=?",
        (operation_id,),
    ) == ((operation_id, "escrow_release", source_reference, setup.poster_id, 100),)

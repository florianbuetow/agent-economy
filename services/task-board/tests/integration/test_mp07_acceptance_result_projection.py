"""Public acceptance replay keeps its immutable operation after task advancement."""

from __future__ import annotations

import importlib
import sqlite3
import uuid
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from task_board_service.core.state import get_app_state

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from typing import Any

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
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


def _rows(
    db_path: str,
    query: str,
    parameters: tuple[object, ...],
) -> tuple[tuple[object, ...], ...]:
    connection = sqlite3.connect(db_path)
    try:
        return tuple(tuple(row) for row in connection.execute(query, parameters).fetchall())
    finally:
        connection.close()


async def test_acceptance_replay_retains_original_operation_after_submission(
    client: AsyncClient,
    gateway_client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    worker_keypair: tuple[Ed25519PrivateKey, str],
    gateway_db_path: str,
) -> None:
    setup = await _create_open_task(client, gateway_client, poster_keypair[0])
    bid = await _create_bid(
        client,
        worker_keypair[0],
        setup,
        operation_id=f"op-{uuid.uuid4()}",
        amount=90,
    )
    accept_operation_id = f"op-{uuid.uuid4()}"
    public_payload = _accept_payload(
        bid,
        operation_id=accept_operation_id,
        expected_lifecycle_version=0,
    )
    path = f"/tasks/{setup.task_id}/bids/{bid.bid_id}/accept"
    initial = await _post_signed(
        client,
        path=path,
        private_key=poster_keypair[0],
        signer_id=setup.poster_id,
        payload=public_payload,
    )
    assert initial.status_code == 200, initial.text
    initial_public = initial.json()

    state = get_app_state()
    store = cast("TaskDbClient", state.store)
    gateway_command: dict[str, Any] = {
        "protocol_version": 1,
        "operation_id": accept_operation_id,
        "task_id": setup.task_id,
        "command": "accept_bid",
        "expected_status": "open",
        "expected_lifecycle_version": 0,
        "actor_kind": "agent",
        "actor_id": setup.poster_id,
        "arguments": {
            "bid_id": bid.bid_id,
            "worker_id": setup.worker_id,
            "escrow_id": initial_public["escrow_id"],
            "reward": initial_public["reward"],
            "bid_amount": bid.amount,
        },
    }
    original_operation = store.claim_task_operation(gateway_command)
    assert original_operation["operation_id"] == accept_operation_id
    assert original_operation["command"] == "accept_bid"
    assert original_operation["operation_state"] == "completed"
    assert original_operation["effect_kind"] == "none"
    assert original_operation["effect_state"] == "none"
    assert original_operation["result"]["status"] == "accepted"
    assert original_operation["result"]["lifecycle_version"] == 1
    assert type(original_operation["result"]["event_id"]) is int

    # Native setup only: public submit rollout is a separate slice. The real
    # Gateway receives owned asset metadata before the native submission.
    store.insert_asset(
        {
            "asset_id": f"asset-{uuid.uuid4()}",
            "task_id": setup.task_id,
            "uploader_id": setup.worker_id,
            "filename": "accepted-work.txt",
            "content_type": "text/plain",
            "size_bytes": 8,
            "storage_path": f"assets/{setup.task_id}/accepted-work.txt",
            "content_hash": "a" * 64,
            "uploaded_at": "2026-10-17T09:00:05Z",
        }
    )
    submit_operation = store.claim_task_operation(
        {
            "protocol_version": 1,
            "operation_id": f"op-{uuid.uuid4()}",
            "task_id": setup.task_id,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": setup.worker_id,
            "arguments": {"worker_id": setup.worker_id},
        }
    )
    assert submit_operation["result"]["status"] == "submitted"
    assert submit_operation["result"]["lifecycle_version"] == 2
    current = store.get_task(setup.task_id)
    assert current is not None
    assert current["status"] == "submitted"
    assert current["lifecycle_version"] == 2

    before_operations = _rows(
        gateway_db_path,
        "SELECT operation_id, command, operation_state, event_id, result_json "
        "FROM board_task_operations WHERE task_id=? ORDER BY operation_id",
        (setup.task_id,),
    )
    before_events = _rows(
        gateway_db_path,
        "SELECT event_id, event_type FROM events WHERE task_id=? "
        "AND event_type IN ('task.accepted', 'task.submitted') ORDER BY event_id",
        (setup.task_id,),
    )

    replay = await _post_signed(
        client,
        path=path,
        private_key=poster_keypair[0],
        signer_id=setup.poster_id,
        payload=public_payload,
    )
    replayed = replay.json()
    after_operations = _rows(
        gateway_db_path,
        "SELECT operation_id, command, operation_state, event_id, result_json "
        "FROM board_task_operations WHERE task_id=? ORDER BY operation_id",
        (setup.task_id,),
    )
    after_events = _rows(
        gateway_db_path,
        "SELECT event_id, event_type FROM events WHERE task_id=? "
        "AND event_type IN ('task.accepted', 'task.submitted') ORDER BY event_id",
        (setup.task_id,),
    )

    assert replay.status_code == 200, replay.text
    assert replayed["status"] == "submitted"
    assert replayed["lifecycle_version"] == 2
    assert before_operations == after_operations
    assert before_events == after_events
    assert [row[1] for row in after_events] == ["task.accepted", "task.submitted"]
    assert {
        "initial_operation": initial_public.get("operation"),
        "replay_operation": replayed.get("operation"),
    } == {
        "initial_operation": original_operation,
        "replay_operation": original_operation,
    }

"""Native bid-bearing tasks expire at their poster-selected acceptance cutoff."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from freezegun import freeze_time

from task_board_service.core.state import get_app_state
from tests.fakes.gateway_task_store import GatewayTaskStore
from tests.unit.routers.conftest import create_task, submit_bid

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

pytestmark = pytest.mark.unit


def _store() -> GatewayTaskStore:
    store = get_app_state().store
    assert isinstance(store, GatewayTaskStore)
    return store


def _release_mock() -> AsyncMock:
    bank = get_app_state().central_bank_client
    assert bank is not None
    release = bank.escrow_release
    assert isinstance(release, AsyncMock)
    return release


async def _task_with_bid(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    bidder_keypair: tuple[Ed25519PrivateKey, str],
    bidder_id: str,
) -> dict[str, Any]:
    """Publish and bid while the shared business clock remains before bidding close."""
    with freeze_time(datetime.now(UTC)):
        task_resp = await create_task(
            client, poster_keypair, poster_id, bidding_deadline_seconds=60
        )
        assert task_resp.status_code == 201, task_resp.text
        task = task_resp.json()
        bid_resp = await submit_bid(client, bidder_keypair, bidder_id, task["task_id"])
        assert bid_resp.status_code == 201, bid_resp.text
    return dict(task)


async def test_open_task_with_bids_expires_at_acceptance_deadline(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    task = await _task_with_bid(client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id)
    with freeze_time(task["acceptance_deadline"]):
        response = await client.get(f"/tasks/{task['task_id']}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "expired"
    assert body["expired_at"] == task["acceptance_deadline"]
    assert body["bid_count"] == 1


async def test_expiry_with_bids_releases_escrow_to_poster_exactly_once(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    task = await _task_with_bid(client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id)
    task_id = task["task_id"]
    store = _store()
    balance_before = store.writer.connection.execute(
        "SELECT balance FROM bank_accounts WHERE account_id=?", (alice_agent_id,)
    ).fetchone()[0]
    release = _release_mock()
    release.reset_mock()
    with freeze_time(task["acceptance_deadline"]):
        first = await client.get(f"/tasks/{task_id}")
        assert first.json()["status"] == "expired"
        release.assert_awaited_once()
        assert release.await_args is not None
        kwargs = release.await_args.kwargs
        assert kwargs["recipient_account_id"] == alice_agent_id
        operation_id = kwargs["operation_id"]
        rows = store.writer.connection.execute(
            "SELECT r.operation_kind,l.account_id,l.amount,o.operation_state,e.status,"
            "e.resolved_at,a.balance FROM bank_operation_receipts AS r "
            "JOIN bank_operation_legs AS l ON l.operation_id=r.operation_id "
            "JOIN board_task_operations AS o ON o.operation_id=r.operation_id "
            "JOIN bank_escrow AS e ON e.escrow_id=r.escrow_id "
            "JOIN bank_accounts AS a ON a.account_id=l.account_id WHERE r.operation_id=?",
            (operation_id,),
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            (
                "escrow_release",
                alice_agent_id,
                task["reward"],
                "completed",
                "released",
                task["acceptance_deadline"],
                balance_before + task["reward"],
            )
        ]
        committed = tuple(store.writer.connection.iterdump())
        second = await client.get(f"/tasks/{task_id}")
        assert second.json()["status"] == "expired"
        release.assert_awaited_once()
        assert tuple(store.writer.connection.iterdump()) == committed


async def test_expiry_with_bids_emits_task_expired_event(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
) -> None:
    task = await _task_with_bid(client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id)
    with freeze_time(task["acceptance_deadline"]):
        response = await client.get(f"/tasks/{task['task_id']}")
    assert response.json()["status"] == "expired"
    events = (
        _store()
        .writer.connection.execute(
            "SELECT e.event_source,e.event_type,e.timestamp,o.command,o.operation_state "
            "FROM events AS e JOIN board_task_operations AS o ON o.event_id=e.event_id "
            "WHERE e.task_id=? AND e.event_type='task.expired'",
            (task["task_id"],),
        )
        .fetchall()
    )
    assert [tuple(row) for row in events] == [
        ("board", "task.expired", task["acceptance_deadline"], "expire_acceptance", "completed")
    ]


async def test_zero_bid_expiry_still_works(
    client: AsyncClient, alice_keypair: tuple[Ed25519PrivateKey, str], alice_agent_id: str
) -> None:
    task_resp = await create_task(
        client, alice_keypair, alice_agent_id, bidding_deadline_seconds=60
    )
    assert task_resp.status_code == 201, task_resp.text
    task = task_resp.json()
    with freeze_time(task["bidding_deadline"]):
        response = await client.get(f"/tasks/{task['task_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "expired"
    assert response.json()["bid_count"] == 0


@pytest.mark.parametrize("boundary", ["bidding_deadline", "acceptance_deadline"])
async def test_open_task_before_its_selected_cutoff_does_not_expire(
    client: AsyncClient,
    alice_keypair: tuple[Ed25519PrivateKey, str],
    alice_agent_id: str,
    bob_keypair: tuple[Ed25519PrivateKey, str],
    bob_agent_id: str,
    boundary: str,
) -> None:
    task = await _task_with_bid(client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id)
    before_cutoff = datetime.fromisoformat(task[boundary]) - timedelta(microseconds=1)
    release = _release_mock()
    release.reset_mock()
    before = tuple(_store().writer.connection.iterdump())
    with freeze_time(before_cutoff):
        response = await client.get(f"/tasks/{task['task_id']}")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "open"
    release.assert_not_awaited()
    assert tuple(_store().writer.connection.iterdump()) == before

"""Expiry of open tasks that carry bids (T-035 / GAP-A2).

An open task whose bidding deadline has passed must expire regardless of how many
bids it attracted, releasing the locked escrow back to the poster exactly once.
Before T-035 the deadline evaluator only expired zero-bid tasks, so a task that
attracted bids but was never accepted stayed open forever with its escrow locked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from task_board_service.core.state import get_app_state
from task_board_service.services.task_db_client import _LIFECYCLE_EVENTS_BY_STATUS
from tests.unit.routers.conftest import create_task, submit_bid

# A short deadline plus an overshoot exercises the lazy-evaluation trigger without
# a scheduler. BID-12 uses the same technique.
_BIDDING_DEADLINE_SECONDS = 1
_DEADLINE_OVERSHOOT_SECONDS = 1.5


async def _task_with_bid_past_bidding_deadline(
    client,
    poster_keypair,
    poster_id,
    bidder_keypair,
    bidder_id,
):
    """Create an open task, attract one bid, then let the bidding deadline pass."""
    task_resp = await create_task(
        client,
        poster_keypair,
        poster_id,
        bidding_deadline_seconds=_BIDDING_DEADLINE_SECONDS,
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["task_id"]

    bid_resp = await submit_bid(client, bidder_keypair, bidder_id, task_id)
    assert bid_resp.status_code == 201

    await asyncio.sleep(_DEADLINE_OVERSHOOT_SECONDS)
    return task_id


@pytest.mark.unit
async def test_open_task_with_bids_expires_after_bidding_deadline(
    client,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
):
    """An open task carrying bids expires on the next read once bidding closes."""
    task_id = await _task_with_bid_past_bidding_deadline(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )

    response = await client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "expired"
    assert body["expired_at"] is not None
    assert body["bid_count"] == 1


@pytest.mark.unit
async def test_expiry_with_bids_releases_escrow_to_poster_exactly_once(
    client,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
):
    """Escrow goes to the poster once; a second read must not release it again."""
    task_id = await _task_with_bid_past_bidding_deadline(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    central_bank = get_app_state().central_bank_client
    central_bank.escrow_release.reset_mock()

    first_read = await client.get(f"/tasks/{task_id}")

    assert first_read.json()["status"] == "expired"
    central_bank.escrow_release.assert_awaited_once()
    release_kwargs = central_bank.escrow_release.await_args.kwargs
    assert release_kwargs["recipient_account_id"] == alice_agent_id

    second_read = await client.get(f"/tasks/{task_id}")

    assert second_read.json()["status"] == "expired"
    central_bank.escrow_release.assert_awaited_once()


@pytest.mark.unit
async def test_expiry_with_bids_emits_task_expired_event(
    client,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
):
    """The single expiry write carries the status that maps to the task.expired event."""
    store = get_app_state().store
    update_spy = MagicMock(wraps=store.update_task)
    store.update_task = update_spy

    task_id = await _task_with_bid_past_bidding_deadline(
        client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
    )
    response = await client.get(f"/tasks/{task_id}")

    assert response.json()["status"] == "expired"

    expiry_updates = [
        call.args[1]
        for call in update_spy.call_args_list
        if len(call.args) > 1 and call.args[1].get("status") == "expired"
    ]
    assert len(expiry_updates) == 1
    assert _LIFECYCLE_EVENTS_BY_STATUS[expiry_updates[0]["status"]] == "task.expired"


@pytest.mark.unit
async def test_zero_bid_expiry_still_works(client, alice_keypair, alice_agent_id):
    """The pre-existing zero-bid expiry path keeps working unchanged."""
    task_resp = await create_task(
        client,
        alice_keypair,
        alice_agent_id,
        bidding_deadline_seconds=_BIDDING_DEADLINE_SECONDS,
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["task_id"]

    await asyncio.sleep(_DEADLINE_OVERSHOOT_SECONDS)
    response = await client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "expired"
    assert body["bid_count"] == 0


@pytest.mark.unit
async def test_open_task_within_bidding_deadline_does_not_expire(
    client,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
):
    """A bid-carrying task still inside its bidding window stays open."""
    task_resp = await create_task(
        client,
        alice_keypair,
        alice_agent_id,
        bidding_deadline_seconds=3600,
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["task_id"]

    bid_resp = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
    assert bid_resp.status_code == 201

    central_bank = get_app_state().central_bank_client
    central_bank.escrow_release.reset_mock()

    response = await client.get(f"/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "open"
    central_bank.escrow_release.assert_not_awaited()

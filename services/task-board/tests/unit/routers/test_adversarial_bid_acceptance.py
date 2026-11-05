"""Adversarial acceptance races with multiple binding bids."""

from __future__ import annotations

import asyncio

import pytest

from tests.unit.routers.conftest import accept_bid, create_task, submit_bid

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _task_with_equal_bids(
    client,
    alice_keypair,
    alice_agent_id: str,
    bob_keypair,
    bob_agent_id: str,
    carol_keypair,
    carol_agent_id: str,
    *,
    reverse_arrival: bool = False,
) -> tuple[str, dict[str, str]]:
    created = await create_task(client, alice_keypair, alice_agent_id)
    assert created.status_code == 201
    task_id = created.json()["result"]["task_id"]
    bidders = [
        ("bob", bob_keypair, bob_agent_id),
        ("carol", carol_keypair, carol_agent_id),
    ]
    if reverse_arrival:
        bidders.reverse()
    bid_ids: dict[str, str] = {}
    for name, keypair, agent_id in bidders:
        response = await submit_bid(client, keypair, agent_id, task_id, amount=60)
        assert response.status_code == 201
        bid_ids[name] = response.json()["bid_id"]
    return task_id, bid_ids


async def test_two_tabs_accepting_different_bids_have_one_serial_winner(
    client,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
    carol_keypair,
    carol_agent_id,
) -> None:
    """AUC-02: two acceptance commands cannot choose two workers."""
    task_id, bids = await _task_with_equal_bids(
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    )

    responses = await asyncio.gather(
        accept_bid(client, alice_keypair, alice_agent_id, task_id, bids["bob"]),
        accept_bid(client, alice_keypair, alice_agent_id, task_id, bids["carol"]),
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = next(response.json() for response in responses if response.status_code == 200)
    conflict = next(response.json() for response in responses if response.status_code == 409)
    assert conflict["error"] in {"invalid_status", "task_version_conflict"}
    task = (await client.get(f"/tasks/{task_id}")).json()
    assert task["status"] == "accepted"
    assert task["accepted_bid_id"] == winner["accepted_bid_id"]
    assert task["worker_id"] == winner["worker_id"]
    assert task["accepted_price"] == 60


async def test_equal_bid_arrival_order_does_not_override_explicit_choice(
    client,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
    carol_keypair,
    carol_agent_id,
) -> None:
    """AUC-03: the poster names the winner; equal-price arrival order does not."""
    first_task, first_bids = await _task_with_equal_bids(
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
    )
    second_task, second_bids = await _task_with_equal_bids(
        client,
        alice_keypair,
        alice_agent_id,
        bob_keypair,
        bob_agent_id,
        carol_keypair,
        carol_agent_id,
        reverse_arrival=True,
    )

    first = await accept_bid(client, alice_keypair, alice_agent_id, first_task, first_bids["carol"])
    second = await accept_bid(
        client, alice_keypair, alice_agent_id, second_task, second_bids["bob"]
    )
    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["worker_id"] == carol_agent_id
    assert second.json()["worker_id"] == bob_agent_id
    assert first.json()["accepted_price"] == second.json()["accepted_price"] == 60

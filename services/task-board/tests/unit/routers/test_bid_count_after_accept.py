"""Spec BA-10: accepting a bid updates bid_count correctly.

`docs/specifications/service-tests/task-board-service-tests.md` BA-10:

    Setup: Alice creates a task. Bob and Carol bid (bid_count = 2). Alice accepts
    Bob's bid.
    Action: GET /tasks/{task_id}
    Expected: bid_count remains 2 (bid_count reflects total bids, not pending bids).

This case had no coverage: the test carrying the BA-10 label asserted an unrelated
(and, per LIFE-07, incorrect) claim about accepting bids after the bidding deadline.
"""

from __future__ import annotations

import pytest

from tests.unit.routers.conftest import accept_bid, create_task, submit_bid


@pytest.mark.unit
async def test_ba_10_bid_count_reflects_total_bids_after_acceptance(
    client,
    alice_keypair,
    alice_agent_id,
    bob_keypair,
    bob_agent_id,
    carol_keypair,
    carol_agent_id,
) -> None:
    """bid_count stays at the total number of bids once one of them is accepted."""
    task_resp = await create_task(client, alice_keypair, alice_agent_id)
    assert task_resp.status_code == 201
    task_id = task_resp.json()["task_id"]

    bob_bid = await submit_bid(client, bob_keypair, bob_agent_id, task_id)
    assert bob_bid.status_code == 201
    bid_id = bob_bid.json()["bid_id"]

    carol_bid = await submit_bid(client, carol_keypair, carol_agent_id, task_id)
    assert carol_bid.status_code == 201

    before = await client.get(f"/tasks/{task_id}")
    assert before.json()["bid_count"] == 2

    accepted = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    after = await client.get(f"/tasks/{task_id}")
    body = after.json()
    assert body["bid_count"] == 2
    assert body["worker_id"] == bob_agent_id
    assert body["accepted_bid_id"] == bid_id

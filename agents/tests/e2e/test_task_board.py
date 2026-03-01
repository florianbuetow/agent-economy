from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_post_task_with_escrow(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster DJB", balance=5000)
        agents_to_close.append(poster)

        task = await poster.post_task(
            title="Test task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        assert task["status"] == "open"
        assert str(task["task_id"]).startswith("t-")
        assert isinstance(task.get("escrow_id"), str)
        assert task["escrow_id"] != ""

        tasks = await poster.list_tasks()
        assert any(item["task_id"] == task["task_id"] for item in tasks)

        balance = await poster.get_balance()
        assert balance["balance"] == 4500
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_submit_bid_on_task(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster OQK", balance=5000)
        worker = await make_funded_agent(name="Worker OQK", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Bid test task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)

        assert isinstance(bid.get("bid_id"), str)
        assert bid["bid_id"] != ""
        assert bid["bidder_id"] == worker.agent_id
        assert bid["amount"] == 400

        bids = await poster.list_bids(task_id=task["task_id"])
        assert any(item["bid_id"] == bid["bid_id"] for item in bids)
        assert len(bids) == 1
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_accept_bid_and_assign_worker(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster DEG", balance=5000)
        worker = await make_funded_agent(name="Worker DEG", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Accept bid task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)

        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        accepted_task = await poster.get_task(task["task_id"])
        assert accepted_task["status"] == "accepted"
        assert accepted_task["worker_id"] == worker.agent_id
        assert accepted_task["accepted_bid_id"] == bid["bid_id"]
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_post_and_cancel_task(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster M6I", balance=5000)
        agents_to_close.append(poster)

        task = await poster.post_task(
            title="Cancel task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        balance_before_cancel = await poster.get_balance()
        assert balance_before_cancel["balance"] == 4500

        await poster.cancel_task(task_id=task["task_id"])

        cancelled_task = await poster.get_task(task["task_id"])
        assert cancelled_task["status"] == "cancelled"

        balance_after_cancel = await poster.get_balance()
        assert balance_after_cancel["balance"] == 5000
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_happy_path_submit_and_approve(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster QIC", balance=5000)
        worker = await make_funded_agent(name="Worker QIC", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Happy path task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        await worker.upload_asset(task["task_id"], "result.txt", b"Hello World")
        await worker.submit_deliverable(task["task_id"])

        submitted_task = await poster.get_task(task["task_id"])
        assert submitted_task["status"] == "submitted"

        await poster.approve_task(task["task_id"])

        approved_task = await poster.get_task(task["task_id"])
        assert approved_task["status"] == "approved"

        worker_balance = await worker.get_balance()
        assert worker_balance["balance"] == 500

        poster_balance = await poster.get_balance()
        assert poster_balance["balance"] == 4500
    finally:
        await _close_agents(agents_to_close)

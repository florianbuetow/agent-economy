from __future__ import annotations

import asyncio
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


@pytest.mark.e2e
async def test_competing_bidders(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster BOT", balance=5000)
        worker1 = await make_funded_agent(name="Worker BOT 1", balance=0)
        worker2 = await make_funded_agent(name="Worker BOT 2", balance=0)
        worker3 = await make_funded_agent(name="Worker BOT 3", balance=0)
        agents_to_close.extend([poster, worker1, worker2, worker3])

        task = await poster.post_task(
            title="Competing bidders task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )

        bid1 = await worker1.submit_bid(task_id=task["task_id"], amount=300)
        bid2 = await worker2.submit_bid(task_id=task["task_id"], amount=400)
        bid3 = await worker3.submit_bid(task_id=task["task_id"], amount=350)

        bids_before_accept = await poster.list_bids(task_id=task["task_id"])
        assert len(bids_before_accept) == 3

        await poster.accept_bid(task_id=task["task_id"], bid_id=bid2["bid_id"])

        accepted_task = await poster.get_task(task["task_id"])
        assert accepted_task["worker_id"] == worker2.agent_id

        bids_after_accept = await poster.list_bids(task_id=task["task_id"])
        bid_ids = {item["bid_id"] for item in bids_after_accept}
        assert len(bids_after_accept) == 3
        assert bid1["bid_id"] in bid_ids
        assert bid2["bid_id"] in bid_ids
        assert bid3["bid_id"] in bid_ids
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_sealed_bid_visibility(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster CIO", balance=5000)
        worker = await make_funded_agent(name="Worker CIO", balance=0)
        observer = await make_funded_agent(name="Observer CIO", balance=0)
        agents_to_close.extend([poster, worker, observer])

        task = await poster.post_task(
            title="Sealed bid task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)

        observer_headers = observer._auth_header(
            {
                "action": "list_bids",
                "task_id": task["task_id"],
                "poster_id": observer.agent_id,
            }
        )
        observer_response = await observer._request_raw(
            "GET",
            f"{observer.config.task_board_url}/tasks/{task['task_id']}/bids",
            headers=observer_headers,
        )
        assert observer_response.status_code == 403

        poster_bids = await poster.list_bids(task_id=task["task_id"])
        assert len(poster_bids) == 1
        assert poster_bids[0]["bid_id"] == bid["bid_id"]
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_file_dispute(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster 8BX", balance=5000)
        worker = await make_funded_agent(name="Worker 8BX", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Dispute task",
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

        await poster.dispute_task(task_id=task["task_id"], reason="Deliverable does not meet spec")

        disputed_task = await poster.get_task(task["task_id"])
        assert disputed_task["status"] == "disputed"
        assert disputed_task["dispute_reason"] == "Deliverable does not meet spec"
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_execution_deadline_expiry(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster NB6", balance=5000)
        worker = await make_funded_agent(name="Worker NB6", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Execution deadline task",
            spec="Do something quickly",
            reward=500,
            bidding_deadline_seconds=5,
            execution_deadline_seconds=2,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])

        await asyncio.sleep(5)

        expired_task = await poster.get_task(task["task_id"])
        assert expired_task["status"] == "expired"

        poster_balance = await poster.get_balance()
        assert poster_balance["balance"] == 5000
    finally:
        await _close_agents(agents_to_close)


@pytest.mark.e2e
async def test_auto_approve_on_review_timeout(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster ZYY", balance=5000)
        worker = await make_funded_agent(name="Worker ZYY", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Review timeout task",
            spec="Do something quickly",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=2,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
        await worker.upload_asset(task["task_id"], "result.txt", b"Hello World")
        await worker.submit_deliverable(task["task_id"])

        await asyncio.sleep(5)

        approved_task = await poster.get_task(task["task_id"])
        assert approved_task["status"] == "approved"

        worker_balance = await worker.get_balance()
        assert worker_balance["balance"] == 500
    finally:
        await _close_agents(agents_to_close)

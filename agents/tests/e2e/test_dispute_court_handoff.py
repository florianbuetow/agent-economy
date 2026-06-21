from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_task_board_dispute_auto_files_court_claim(make_funded_agent) -> None:
    """Task Board dispute creates exactly one Court dispute for the task."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster DCH", balance=5000)
        worker = await make_funded_agent(name="Worker DCH", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Dispute Court handoff task",
            spec="Deliver the requested artifact.",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid = await worker.submit_bid(task_id=task["task_id"], amount=400)
        await poster.accept_bid(task_id=task["task_id"], bid_id=bid["bid_id"])
        await worker.upload_asset(task["task_id"], "result.txt", b"incomplete result")
        await worker.submit_deliverable(task["task_id"])

        dispute_response = await poster.dispute_task(
            task_id=task["task_id"],
            reason="The submitted artifact is incomplete.",
        )

        assert dispute_response["status"] == "disputed"
        assert isinstance(dispute_response["dispute_id"], str)
        assert dispute_response["dispute_id"] != ""

        disputes = await poster.list_disputes(task_id=str(task["task_id"]))
        assert len(disputes) == 1
        assert disputes[0]["dispute_id"] == dispute_response["dispute_id"]
        assert disputes[0]["task_id"] == task["task_id"]
    finally:
        await _close_agents(agents_to_close)

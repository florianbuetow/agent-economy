from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .native_task_contract import lifecycle_version, new_operation_id, publish_task

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

        task = await publish_task(
            poster,
            title="Dispute Court handoff task",
            spec="Deliver the requested artifact.",
            reward=500,
            bidding_deadline_seconds=3600,
            acceptance_window_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        bid_response = await worker.submit_bid(
            task_id=task["task_id"],
            amount=400,
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(task),
        )
        assert bid_response.status_code == 201
        bid = bid_response.body
        accept_response = await poster.accept_bid(
            task_id=task["task_id"],
            bid_id=bid["bid_id"],
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(task),
        )
        assert accept_response.status_code == 200
        accepted_task = accept_response.body
        assert accepted_task["status"] == "accepted"
        await worker.upload_asset(task["task_id"], "result.txt", b"incomplete result")
        submit_response = await worker.submit_deliverable(
            task["task_id"],
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(accepted_task),
        )
        assert submit_response.status_code == 200
        submitted_task = submit_response.body
        assert submitted_task["status"] == "submitted"

        dispute_response = await poster.dispute_task(
            task_id=task["task_id"],
            reason="The submitted artifact is incomplete.",
            operation_id=new_operation_id(),
            expected_lifecycle_version=lifecycle_version(submitted_task),
        )
        assert dispute_response.status_code == 200
        disputed_task = dispute_response.body

        assert disputed_task["status"] == "disputed"
        assert isinstance(disputed_task["dispute_id"], str)
        assert disputed_task["dispute_id"] != ""

        disputes = await poster.list_disputes(task_id=str(task["task_id"]))
        assert len(disputes) == 1
        assert disputes[0]["dispute_id"] == disputed_task["dispute_id"]
        assert disputes[0]["task_id"] == task["task_id"]
    finally:
        await _close_agents(agents_to_close)

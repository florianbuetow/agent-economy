from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_mutual_feedback_exchange(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster 86U", balance=5000)
        worker = await make_funded_agent(name="Worker 86U", balance=0)
        agents_to_close.extend([poster, worker])

        task = await poster.post_task(
            title="Feedback task",
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
        await poster.approve_task(task["task_id"])

        await poster.submit_feedback(
            task_id=task["task_id"],
            to_agent_id=str(worker.agent_id),
            category="delivery_quality",
            rating="extremely_satisfied",
            comment="Great work",
        )
        await worker.submit_feedback(
            task_id=task["task_id"],
            to_agent_id=str(poster.agent_id),
            category="spec_quality",
            rating="satisfied",
            comment="Clear spec",
        )

        task_feedback = await poster.get_task_feedback(task["task_id"])
        assert any(
            item["task_id"] == task["task_id"]
            and item["from_agent_id"] == poster.agent_id
            and item["to_agent_id"] == worker.agent_id
            for item in task_feedback
        )
        assert any(
            item["task_id"] == task["task_id"]
            and item["from_agent_id"] == worker.agent_id
            and item["to_agent_id"] == poster.agent_id
            for item in task_feedback
        )

        worker_feedback = await poster.get_agent_feedback(str(worker.agent_id))
        assert any(
            item["task_id"] == task["task_id"]
            and item["from_agent_id"] == poster.agent_id
            and item["to_agent_id"] == worker.agent_id
            for item in worker_feedback
        )

        poster_feedback = await poster.get_agent_feedback(str(poster.agent_id))
        assert any(
            item["task_id"] == task["task_id"]
            and item["from_agent_id"] == worker.agent_id
            and item["to_agent_id"] == poster.agent_id
            for item in poster_feedback
        )
    finally:
        await _close_agents(agents_to_close)

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_sealed_feedback_invisible_until_mutual(make_funded_agent) -> None:
    """Confirm one-sided feedback is sealed; becomes visible when both sides submit."""
    agents_to_close: list[BaseAgent] = []

    try:
        poster = await make_funded_agent(name="Poster RS1", balance=5000)
        worker = await make_funded_agent(name="Worker RS1", balance=0)
        agents_to_close.extend([poster, worker])

        # Complete a task through the happy path
        task = await poster.post_task(
            title="Sealed feedback task",
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

        # Poster submits feedback (one-sided — should be sealed)
        poster_fb = await poster.submit_feedback(
            task_id=task["task_id"],
            to_agent_id=str(worker.agent_id),
            category="delivery_quality",
            rating="satisfied",
            comment="Good work",
        )
        poster_fb_id = poster_fb["feedback_id"]

        # Sealed: task feedback query should return empty (no visible records)
        task_feedback_before = await poster.get_task_feedback(task["task_id"])
        visible_before = [fb for fb in task_feedback_before if fb.get("visible", True)]
        assert len(visible_before) == 0, "One-sided feedback should be sealed (invisible)"

        # Sealed: direct lookup should return 404
        sealed_response = await poster._request_raw(
            "GET",
            f"{poster.config.reputation_url}/feedback/{poster_fb_id}",
        )
        assert sealed_response.status_code == 404, (
            "Sealed feedback should be indistinguishable from non-existent"
        )

        # Worker submits feedback (mutual — both should become visible)
        await worker.submit_feedback(
            task_id=task["task_id"],
            to_agent_id=str(poster.agent_id),
            category="spec_quality",
            rating="satisfied",
            comment="Clear spec",
        )

        # Now both should be visible
        task_feedback_after = await poster.get_task_feedback(task["task_id"])
        assert len(task_feedback_after) == 2, "Both feedbacks should be visible after mutual submission"

        # Direct lookup should now succeed
        revealed_response = await poster._request_raw(
            "GET",
            f"{poster.config.reputation_url}/feedback/{poster_fb_id}",
        )
        assert revealed_response.status_code == 200, "Feedback should be revealed after mutual submission"
    finally:
        await _close_agents(agents_to_close)

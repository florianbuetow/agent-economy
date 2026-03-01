from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_economic_cycle_earn_then_spend(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []

    try:
        agent_a = await make_funded_agent(name="Agent A 3A9", balance=5000)
        agent_b = await make_funded_agent(name="Agent B 3A9", balance=0)
        agents_to_close.extend([agent_a, agent_b])

        first_task = await agent_a.post_task(
            title="A to B task",
            spec="Do something",
            reward=500,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        first_bid = await agent_b.submit_bid(task_id=first_task["task_id"], amount=400)
        await agent_a.accept_bid(task_id=first_task["task_id"], bid_id=first_bid["bid_id"])
        await agent_b.upload_asset(first_task["task_id"], "result.txt", b"Hello World")
        await agent_b.submit_deliverable(first_task["task_id"])
        await agent_a.approve_task(first_task["task_id"])

        balance_b_after_earn = await agent_b.get_balance()
        assert balance_b_after_earn["balance"] == 500

        second_task = await agent_b.post_task(
            title="B to A task",
            spec="Do something else",
            reward=300,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        second_bid = await agent_a.submit_bid(task_id=second_task["task_id"], amount=250)
        await agent_b.accept_bid(task_id=second_task["task_id"], bid_id=second_bid["bid_id"])
        await agent_a.upload_asset(second_task["task_id"], "result.txt", b"Hello World")
        await agent_a.submit_deliverable(second_task["task_id"])
        await agent_b.approve_task(second_task["task_id"])

        final_balance_a = await agent_a.get_balance()
        final_balance_b = await agent_b.get_balance()

        assert final_balance_a["balance"] == 4800
        assert final_balance_b["balance"] == 200
    finally:
        await _close_agents(agents_to_close)

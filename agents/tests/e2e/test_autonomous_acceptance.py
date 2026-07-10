"""E2E: the Task Feeder autonomously accepts the winning bid (WP-15 / Q-16).

With the live stack and no demo engine and no human, a posted task is accepted by
the feeder's own acceptance loop — picking the lowest bid — then submitted and
approved through the normal flow.  Before WP-15 the feeder never accepted, so the
task would sit ``open`` until it expired.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from task_feeder.acceptance import AcceptanceLoop
from task_feeder.config import AcceptanceConfig

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent

REWARD = 500
LOW_BID = 6
HIGH_BID = 8
BIDDING_DEADLINE_SECONDS = 120


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_feeder_autonomously_accepts_lowest_bid(make_funded_agent) -> None:
    agents_to_close: list[BaseAgent] = []
    try:
        feeder = await make_funded_agent(name="WP15 Feeder", balance=5000)
        worker_low = await make_funded_agent(name="WP15 Worker Low", balance=1000)
        worker_high = await make_funded_agent(name="WP15 Worker High", balance=1000)
        agents_to_close.extend([feeder, worker_low, worker_high])

        assert worker_low.agent_id is not None
        low_balance_before = (await worker_low.get_balance())["balance"]

        task = await feeder.post_task(
            title="WP15 autonomous acceptance",
            spec="Compute 2 + 2",
            reward=REWARD,
            bidding_deadline_seconds=BIDDING_DEADLINE_SECONDS,
            execution_deadline_seconds=600,
            review_deadline_seconds=300,
        )
        task_id = task["task_id"]

        # Two competing bids; the lower one must win the undercutting contest.
        await worker_high.submit_bid(task_id=task_id, amount=HIGH_BID)
        await worker_low.submit_bid(task_id=task_id, amount=LOW_BID)

        # Quorum of 2 bids makes the task eligible immediately; the acceptance
        # window (60s) stays well inside the 120s bidding deadline (T-035).
        acceptance_config = AcceptanceConfig(
            acceptance_after_seconds=60,
            acceptance_poll_interval_seconds=1,
            min_bids_to_accept=2,
            bidding_deadline_seconds=BIDDING_DEADLINE_SECONDS,
        )
        loop = AcceptanceLoop(
            agent=feeder,
            config=acceptance_config,
            now=lambda: datetime.now(UTC),
        )

        # The feeder accepts autonomously — no demo engine, no human.
        await loop.process_open_tasks()

        accepted = await feeder.get_task(task_id)
        assert accepted["status"] == "accepted"
        assert accepted["worker_id"] == worker_low.agent_id

        # Normal flow to completion: winner delivers, poster approves, winner paid.
        await worker_low.upload_asset(task_id, "result.txt", b"4")
        await worker_low.submit_deliverable(task_id)
        await feeder.approve_task(task_id)

        completed = await feeder.get_task(task_id)
        assert completed["status"] == "approved"

        low_balance_after = (await worker_low.get_balance())["balance"]
        assert low_balance_after == low_balance_before + REWARD
    finally:
        await _close_agents(agents_to_close)

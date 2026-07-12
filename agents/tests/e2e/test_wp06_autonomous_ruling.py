"""WP-06.1 (GAP-A1): a disputed task reaches ``ruled`` with no demo engine.

This is the proof that closes GAP-A1. Poster and worker are driven purely through
the SDK; the dispute is filed and rebutted, and then the test only *polls*
``get_task``. Nothing calls ``trigger_ruling`` and no demo engine runs — the ruling
is fired by the Task Board deadline evaluator on a lazy read once the rebuttal exists.

On code without the evaluator ruling trigger this task stays ``disputed`` forever and
the poll loop times out (red); with the trigger it converges to ``ruled`` (green).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from base_agent.platform import PlatformAgent


async def _close_agents(agents_to_close: list[BaseAgent]) -> None:
    for agent in agents_to_close:
        await agent.close()


@pytest.mark.e2e
async def test_disputed_task_reaches_ruled_via_polling(
    make_funded_agent,
    platform_agent: PlatformAgent,
) -> None:
    """A disputed+rebutted task reaches ``ruled`` via polling alone (no explicit ruling)."""
    _ = platform_agent  # session fixture ensures the platform agent is registered
    agents_to_close: list[BaseAgent] = []
    try:
        poster = await make_funded_agent(name="Poster WP06A1", balance=5000)
        worker = await make_funded_agent(name="Worker WP06A1", balance=0)
        agents_to_close.extend([poster, worker])

        reward = 1000
        task = await poster.post_task(
            title="WP06 GAP-A1 task",
            spec="Implement the addition function as specified",
            reward=reward,
            bidding_deadline_seconds=3600,
            execution_deadline_seconds=7200,
            review_deadline_seconds=3600,
        )
        task_id = str(task["task_id"])

        bid = await worker.submit_bid(task_id=task_id, amount=reward)
        await poster.accept_bid(task_id=task_id, bid_id=str(bid["bid_id"]))
        await worker.upload_asset(task_id, "solution.txt", b"def add(a, b):\n    return a + b\n")
        await worker.submit_deliverable(task_id)

        # Poster disputes; Task Board files the Court claim and persists the dispute id
        # plus the rebuttal deadline.
        await poster.dispute_task(task_id=task_id, reason="Deliverable does not meet spec")
        disputed = await poster.get_task(task_id)
        assert disputed["status"] == "disputed"
        dispute_id = str(disputed["dispute_id"])
        assert dispute_id != ""

        # Worker rebuts through Task Board (platform forwards to Court).
        await worker.submit_worker_rebuttal(task_id, dispute_id, "The deliverable meets the spec.")

        # No trigger_ruling call, no demo engine — only polling reads. The lazy deadline
        # evaluator must fire the platform-signed ruling once the rebuttal exists.
        ruled = None
        for _ in range(40):
            current = await poster.get_task(task_id)
            if current["status"] == "ruled":
                ruled = current
                break
            await asyncio.sleep(0.5)

        assert ruled is not None, "disputed task never reached 'ruled' via polling (GAP-A1 open)"
        assert isinstance(ruled["worker_pct"], int)
        assert 0 <= ruled["worker_pct"] <= 100
        assert isinstance(ruled.get("ruling_id"), str)
        assert ruled["ruling_id"] != ""
        assert ruled.get("ruled_at") is not None
    finally:
        await _close_agents(agents_to_close)

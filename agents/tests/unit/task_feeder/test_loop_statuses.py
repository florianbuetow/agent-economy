from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import task_feeder.review as review_module
from task_feeder.config import TaskFeederConfig
from task_feeder.loop import TaskFeederLoop
from task_feeder.review import ReviewLoop


def _make_config(**overrides: object) -> TaskFeederConfig:
    defaults = {
        "handle": "feeder",
        "tasks_file": "../data/math_tasks.jsonl",
        "feed_interval_seconds": 15,
        "max_open_tasks": 5,
        "bidding_deadline_seconds": 120,
        "execution_deadline_seconds": 300,
        "review_deadline_seconds": 120,
        "base_reward": 10,
        "reward_per_level": 10,
        "shuffle": True,
    }
    defaults.update(overrides)
    return TaskFeederConfig(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
class TestTaskFeederStatusVocabulary:
    @pytest.mark.asyncio
    async def test_count_open_tasks_queries_open_status(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-feeder"
        agent.list_tasks = AsyncMock(return_value=[])

        loop = TaskFeederLoop.__new__(TaskFeederLoop)
        loop._agent = agent
        loop._config = _make_config()

        count = await loop._count_open_tasks()

        assert count == 0
        agent.list_tasks.assert_awaited_once_with(status="open", poster_id="a-feeder")

    @pytest.mark.asyncio
    async def test_review_loop_queries_submitted_status(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-feeder"
        agent.list_tasks = AsyncMock(return_value=[])
        loop = ReviewLoop(agent, {})

        async def stop_after_first_poll(interval_seconds: int) -> None:
            assert interval_seconds == 0
            loop.stop()

        original_sleep = review_module.asyncio.sleep
        review_module.asyncio.sleep = stop_after_first_poll
        try:
            await loop.run(interval_seconds=0)
        finally:
            review_module.asyncio.sleep = original_sleep

        agent.list_tasks.assert_awaited_once_with(status="submitted", poster_id="a-feeder")

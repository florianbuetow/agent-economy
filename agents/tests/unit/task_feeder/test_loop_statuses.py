from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

import task_feeder.review as review_module
from task_feeder.config import TaskFeederConfig
from task_feeder.loop import TaskFeederLoop
from task_feeder.review import ReviewLoop

from ._runtime_fixtures import POSTER_ID, make_agent, runtime_commands

if TYPE_CHECKING:
    from pathlib import Path


def _make_config(**overrides: object) -> TaskFeederConfig:
    defaults = {
        "handle": "feeder",
        "tasks_file": "../data/math_tasks.jsonl",
        "source_max_bytes": 8_388_608,
        "feed_interval_seconds": 15,
        "max_open_tasks": 5,
        "bidding_deadline_seconds": 120,
        "acceptance_window_seconds": 120,
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
        agent.agent_id = POSTER_ID
        agent.list_tasks = AsyncMock(return_value=[])

        loop = TaskFeederLoop.__new__(TaskFeederLoop)
        loop._agent = agent
        loop._config = _make_config()

        count = await loop._count_open_tasks()

        assert count == 0
        agent.list_tasks.assert_awaited_once_with(status="open", poster_id=POSTER_ID)

    @pytest.mark.asyncio
    async def test_review_loop_queries_submitted_status(self, tmp_path: Path) -> None:
        agent = make_agent()
        agent.list_tasks = AsyncMock(return_value=[])
        with runtime_commands(tmp_path, agent) as (commands, _store):
            loop = ReviewLoop(agent, {}, commands)

            async def stop_after_first_poll(interval_seconds: int) -> None:
                assert interval_seconds == 0
                loop.stop()

            original_sleep = review_module.asyncio.sleep
            review_module.asyncio.sleep = stop_after_first_poll
            try:
                await loop.run(interval_seconds=0)
            finally:
                review_module.asyncio.sleep = original_sleep

        agent.list_tasks.assert_awaited_once_with(status="submitted", poster_id=POSTER_ID)

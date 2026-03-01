"""Feeder loop — posts tasks from the JSONL file onto the task board."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from base_agent.agent import BaseAgent

from task_feeder.config import TaskFeederConfig
from task_feeder.reader import RawTask, iterate_tasks, load_tasks

logger = logging.getLogger(__name__)


class TaskFeederLoop:
    """Deterministic loop that posts math tasks to the platform.

    Reads from a JSONL file, respects a max-open-tasks cap, and sleeps
    between posts.  Reward is computed from the task's difficulty level.
    """

    def __init__(
        self,
        agent: BaseAgent,
        config: TaskFeederConfig,
    ) -> None:
        self._agent = agent
        self._config = config
        self._running = True
        self._tasks_posted = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Run the feeder loop until stopped."""
        logger.info("Task feeder starting (handle=%s)", self._config.handle)

        tasks_path = Path(self._config.tasks_file)
        if not tasks_path.is_absolute():
            # Resolve relative to agents/ directory (where config.yaml lives)
            agents_dir = Path(__file__).resolve().parents[2]
            tasks_path = (agents_dir / tasks_path).resolve()

        all_tasks = load_tasks(tasks_path)
        task_iter = iterate_tasks(all_tasks, shuffle=self._config.shuffle)

        while self._running:
            try:
                await self._feed_one(task_iter)
            except asyncio.CancelledError:
                logger.info("Feeder cancelled, shutting down")
                self._running = False
            except Exception:
                logger.exception("Unhandled error in feeder cycle")
                await asyncio.sleep(self._config.feed_interval_seconds)

        logger.info("Task feeder stopped. Total tasks posted: %d", self._tasks_posted)

    def stop(self) -> None:
        """Signal the loop to stop after the current cycle."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _feed_one(self, task_iter: Any) -> None:
        """Post one task if the board isn't full, otherwise wait."""

        # Check how many of our open tasks are still in bidding
        open_count = await self._count_open_tasks()
        if open_count >= self._config.max_open_tasks:
            logger.debug(
                "Board has %d open tasks (max %d), waiting",
                open_count,
                self._config.max_open_tasks,
            )
            await asyncio.sleep(self._config.feed_interval_seconds)
            return

        raw_task: RawTask = next(task_iter)
        reward = self._compute_reward(raw_task.level)

        logger.info(
            "Posting task: level=%d type=%s reward=%d title=%r",
            raw_task.level,
            raw_task.problem_type,
            reward,
            raw_task.title,
        )

        result = await self._agent.post_task(
            title=raw_task.title,
            spec=raw_task.spec,
            reward=reward,
            bidding_deadline_seconds=self._config.bidding_deadline_seconds,
            execution_deadline_seconds=self._config.execution_deadline_seconds,
            review_deadline_seconds=self._config.review_deadline_seconds,
        )

        task_id = result.get("task_id", "unknown")
        self._tasks_posted += 1
        logger.info(
            "Posted task %s (#%d): level=%d reward=%d",
            task_id,
            self._tasks_posted,
            raw_task.level,
            reward,
        )

        await asyncio.sleep(self._config.feed_interval_seconds)

    async def _count_open_tasks(self) -> int:
        """Count tasks posted by this agent that are still in bidding."""
        try:
            tasks = await self._agent.list_tasks(
                status="BIDDING",
                poster_id=self._agent.agent_id,
            )
            return len(tasks)
        except Exception:
            logger.warning("Could not count open tasks, assuming 0")
            return 0

    def _compute_reward(self, level: int) -> int:
        """Compute reward from difficulty level.

        ``reward = base_reward + level * reward_per_level``
        """
        return self._config.base_reward + level * self._config.reward_per_level

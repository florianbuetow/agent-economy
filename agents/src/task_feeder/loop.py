"""Task Feeder loop driven by the durable native-posting coordinator."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from base_agent.runtime_intents import RuntimeIntentError

if TYPE_CHECKING:
    from service_auth.posting_journal import RawTask

    from base_agent.agent import BaseAgent
    from task_feeder.config import TaskFeederConfig
    from task_feeder.posting import TaskFeederPostingCoordinator

logger = logging.getLogger(__name__)


class TaskFeederLoop:
    """Resume saved publications and reserve fresh work below the open-task cap."""

    def __init__(
        self,
        agent: BaseAgent,
        config: TaskFeederConfig,
        posting: TaskFeederPostingCoordinator,
    ) -> None:
        self._agent = agent
        self._config = config
        self._posting = posting
        self._running = True
        self._task_map: dict[str, RawTask] = {}

    async def run(self) -> None:
        """Run feeder cycles until stopped, delaying only between cycles."""

        logger.info("Task feeder starting (handle=%s)", self._config.handle)
        while self._running:
            try:
                await self.feed_once()
            except asyncio.CancelledError:
                logger.info("Feeder cancelled, shutting down")
                self._running = False
                continue
            except httpx.HTTPError:
                logger.exception("Recoverable feeder transport failure")
            except RuntimeIntentError as exc:
                if exc.reason not in {"source_conflict", "unresolved_posting"}:
                    raise
                logger.error("Fresh task feeding remains disabled: %s", exc)
            if self._running:
                await asyncio.sleep(self._config.feed_interval_seconds)
        logger.info("Task feeder stopped. Total tasks posted: %d", self._posting.published_count)

    async def feed_once(self) -> None:
        """Resume at most one saved record, otherwise reserve at most one fresh task."""

        resumed = await self._posting.resume_one()
        self.rebuild_task_map()
        if resumed is not None or not self._posting.source_ready:
            return
        open_count = await self._count_open_tasks()
        if open_count >= self._config.max_open_tasks:
            logger.debug(
                "Board has %d open tasks (max %d)",
                open_count,
                self._config.max_open_tasks,
            )
            return
        await self._posting.reserve_and_submit_next()
        self.rebuild_task_map()

    def rebuild_task_map(self) -> dict[str, RawTask]:
        """Synchronize the live review map from durable published records."""

        rebuilt = self._posting.rebuild_task_map()
        self._replace_task_map(rebuilt)
        return dict(self._task_map)

    def stop(self) -> None:
        """Signal the loop to stop after the current cycle."""

        self._running = False

    @property
    def task_map(self) -> dict[str, RawTask]:
        """Return the live map shared with the review loop."""

        return self._task_map

    def _replace_task_map(self, rebuilt: dict[str, RawTask]) -> None:
        self._task_map.clear()
        self._task_map.update(rebuilt)

    async def _count_open_tasks(self) -> int:
        """Count this poster's currently open tasks without failure fallback."""

        tasks = await self._agent.list_tasks(
            status="open",
            poster_id=self._agent.agent_id,
        )
        return len(tasks)

    def _compute_reward(self, level: int) -> int:
        """Retain the explicit configured reward formula for callers and tests."""

        return self._config.base_reward + level * self._config.reward_per_level

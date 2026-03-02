"""Review loop for auto-approving or disputing submitted feeder tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from task_feeder.reader import RawTask

logger = logging.getLogger(__name__)


def check_answer(submitted: str, solutions: list[str]) -> bool:
    """Return True when submitted answer matches any expected solution."""
    normalized_submitted = submitted.strip().lower()
    if normalized_submitted == "":
        return False
    if not solutions:
        return False

    normalized_solutions = [solution.strip().lower() for solution in solutions]
    return normalized_submitted in normalized_solutions


class ReviewLoop:
    """Poll for submitted tasks and auto-review known feeder tasks."""

    def __init__(self, agent: BaseAgent, task_map: dict[str, RawTask]) -> None:
        self._agent = agent
        self._task_map = task_map
        self._running = True

    async def review_one(self, task_id: str) -> str:
        """Review a single submitted task and approve or dispute it."""
        if task_id not in self._task_map:
            msg = f"Unknown task: {task_id}"
            raise LookupError(msg)

        payload = await self._agent.get_task(task_id)
        submitted_answer: str = str(
            payload.get("submitted_answer")
            or payload.get("submission", {}).get("answer")
            or payload.get("deliverable", {}).get("answer")
            or ""
        )

        raw_task = self._task_map[task_id]
        if check_answer(submitted_answer, raw_task.solutions):
            await self._agent.approve_task(task_id)
            return "approved"

        reason = f"Expected one of {raw_task.solutions!r} but got {submitted_answer!r}"
        await self._agent.dispute_task(task_id, reason)
        return "disputed"

    async def run(self, interval_seconds: int) -> None:
        """Continuously review submitted tasks for this poster."""
        while self._running:
            tasks = await self._agent.list_tasks(
                status="SUBMITTED",
                poster_id=self._agent.agent_id,
            )
            for task in tasks:
                task_id = str(task.get("task_id", ""))
                status = str(task.get("status", ""))
                if status.lower() != "submitted":
                    continue
                if task_id not in self._task_map:
                    continue
                try:
                    await self.review_one(task_id)
                except Exception:
                    logger.exception("Failed to review task_id=%s", task_id)

            await asyncio.sleep(interval_seconds)

    def stop(self) -> None:
        """Signal the review loop to stop."""
        self._running = False

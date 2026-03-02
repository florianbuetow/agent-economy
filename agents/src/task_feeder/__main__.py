"""Entry point for the Task Feeder.

Usage::

    cd agents/
    uv run python -m task_feeder
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import httpx

from base_agent.agent import BaseAgent
from base_agent.config import load_agent_config
from task_feeder.config import load_task_feeder_settings
from task_feeder.loop import TaskFeederLoop
from task_feeder.review import ReviewLoop


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )


async def _main() -> None:
    _setup_logging()
    logger = logging.getLogger("task_feeder")

    # Load configuration
    feeder_config = load_task_feeder_settings()
    agent_config = load_agent_config(feeder_config.handle)

    logger.info("Starting Task Feeder (handle=%s)", feeder_config.handle)
    logger.info("Tasks file: %s", feeder_config.tasks_file)

    # Create platform agent and register
    agent = BaseAgent(agent_config)
    await agent.register()
    logger.info("Registered as agent_id=%s", agent.agent_id)
    # Create bank account (idempotent — 409 if already exists)
    try:
        await agent.create_account()
        logger.info("Bank account created for agent_id=%s", agent.agent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            logger.info("Bank account already exists for agent_id=%s", agent.agent_id)
        else:
            raise

    # Create and run the feeder/review loops
    loop = TaskFeederLoop(agent=agent, config=feeder_config)
    review = ReviewLoop(agent=agent, task_map=loop.task_map)

    # Graceful shutdown
    def _handle_signal() -> None:
        logger.info("Received shutdown signal")
        loop.stop()
        review.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, _handle_signal)

    feed_task = asyncio.create_task(loop.run())
    review_task = asyncio.create_task(review.run(feeder_config.review_interval_seconds))

    try:
        await asyncio.gather(feed_task, review_task)
    finally:
        await agent.close()
        logger.info("Task Feeder shut down cleanly")


def main() -> None:
    """Sync entry point."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()

"""Entry point for the Math Worker Agent.

Usage::

    cd agents/
    uv run python -m math_worker
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from base_agent.config import load_agent_config
from base_agent.agent import BaseAgent

from math_worker.config import load_math_worker_settings
from math_worker.llm_client import LLMClient
from math_worker.loop import MathWorkerLoop


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )


async def _main() -> None:
    _setup_logging()
    logger = logging.getLogger("math_worker")

    # Load configuration
    llm_config, worker_config = load_math_worker_settings()
    agent_config = load_agent_config(worker_config.handle)

    logger.info("Starting Math Worker Agent (handle=%s)", worker_config.handle)
    logger.info("LLM endpoint: %s model: %s", llm_config.base_url, llm_config.model_id)

    # Create platform agent and register
    agent = BaseAgent(agent_config)
    await agent.register()
    logger.info("Registered as agent_id=%s", agent.agent_id)

    # Create LLM client
    llm = LLMClient(llm_config)

    # Create and run the loop
    loop = MathWorkerLoop(agent=agent, llm=llm, config=worker_config)

    # Wire up graceful shutdown
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("Received shutdown signal")
        loop.stop()
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, _handle_signal)

    try:
        await loop.run()
    finally:
        await llm.close()
        await agent.close()
        logger.info("Math Worker Agent shut down cleanly")


def main() -> None:
    """Sync entry point."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()

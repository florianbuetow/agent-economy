"""Entry point for the Math Worker Agent.

Usage::

    cd agents/
    uv run python -m math_worker                  # legacy: flat config sections
    uv run python -m math_worker mathbot           # factory: named worker profile
    uv run python -m math_worker mathbot_openai    # factory: different profile
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import httpx

from base_agent.agent import BaseAgent
from base_agent.config import load_agent_config
from base_agent.worker_factory import WorkerFactory
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


async def _register_and_create_account(agent: BaseAgent, logger: logging.Logger) -> None:
    """Register the agent and create a bank account (idempotent)."""
    await agent.register()
    logger.info("Registered as agent_id=%s", agent.agent_id)
    try:
        await agent.create_account()
        logger.info("Bank account created for agent_id=%s", agent.agent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            logger.info("Bank account already exists for agent_id=%s", agent.agent_id)
        else:
            raise


async def _run_loop(
    agent: BaseAgent,
    llm: LLMClient,
    loop: MathWorkerLoop,
    logger: logging.Logger,
) -> None:
    """Run the worker loop with graceful shutdown."""
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


async def _main_factory(worker_name: str) -> None:
    """Launch a math worker via the WorkerFactory."""
    _setup_logging()
    logger = logging.getLogger("math_worker")

    factory = WorkerFactory()
    bundle = factory.create_math_worker(worker_name)

    logger.info(
        "Starting Math Worker Agent via factory (profile=%s, handle=%s)",
        worker_name,
        bundle.agent.name,
    )

    await _register_and_create_account(bundle.agent, logger)
    await _run_loop(bundle.agent, bundle.llm, bundle.loop, logger)


async def _main_legacy() -> None:
    """Launch a math worker via the legacy flat config sections."""
    _setup_logging()
    logger = logging.getLogger("math_worker")

    llm_config, worker_config = load_math_worker_settings()
    agent_config = load_agent_config(worker_config.handle)

    logger.info("Starting Math Worker Agent (handle=%s)", worker_config.handle)
    logger.info("LLM endpoint: %s model: %s", llm_config.base_url, llm_config.model_id)

    agent = BaseAgent(agent_config)
    await _register_and_create_account(agent, logger)

    llm = LLMClient(llm_config)
    loop = MathWorkerLoop(agent=agent, llm=llm, config=worker_config)
    await _run_loop(agent, llm, loop, logger)


def main() -> None:
    """Sync entry point."""
    if len(sys.argv) > 1:
        worker_name = sys.argv[1]
        asyncio.run(_main_factory(worker_name))
    else:
        asyncio.run(_main_legacy())


if __name__ == "__main__":
    main()

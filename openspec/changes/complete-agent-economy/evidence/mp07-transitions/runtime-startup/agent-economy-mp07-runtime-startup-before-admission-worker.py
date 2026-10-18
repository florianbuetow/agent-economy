"""Entry point for the Math Worker Agent.

Usage::

    cd agents/
    uv run python -m math_worker mathbot           # factory: named worker profile
    uv run python -m math_worker mathbot_openai    # factory: different profile

A worker profile name is required. Profiles are defined in config.yaml's
``workers:`` section and validated against roster.yaml.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import TYPE_CHECKING

import httpx
from service_commons.config import get_config_path as resolve_config_path

from base_agent.runtime_intents import FileTaskIntentStore
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent
from base_agent.worker_factory import WorkerFactory
from math_worker.loop import MathWorkerLoop

if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from math_worker.llm_client import LLMClient


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
    loop: MathWorkerLoop,
    logger: logging.Logger,
) -> None:
    """Run the worker loop with graceful shutdown."""

    def _handle_signal() -> None:
        logger.info("Received shutdown signal")
        loop.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, _handle_signal)

    await loop.run()


async def _close_clients(agent: BaseAgent, llm: LLMClient) -> None:
    try:
        await llm.close()
    finally:
        await agent.close()


async def _main_factory(worker_name: str) -> None:
    """Launch a math worker via the WorkerFactory."""
    _setup_logging()
    logger = logging.getLogger("math_worker")

    config_path = resolve_config_path(
        env_var_name="AGENT_CONFIG_PATH", default_filename="config.yaml"
    )
    factory = WorkerFactory(config_path=config_path)
    bundle = factory.create_math_worker(worker_name)

    logger.info(
        "Starting Math Worker Agent via factory (profile=%s, handle=%s)",
        worker_name,
        bundle.agent.name,
    )

    clients_closed = False
    try:
        await _register_and_create_account(bundle.agent, logger)
        owner = runtime_owner_from_agent(
            runtime_config=bundle.runtime_config,
            agent=bundle.agent,
            role="math-worker",
            profile=bundle.profile_name,
            handle=bundle.config.handle,
        )
        with FileTaskIntentStore(
            root_directory=bundle.runtime_config.directory,
            owner=owner,
            diagnostic_limit=bundle.runtime_config.diagnostic_limit,
            max_record_bytes=bundle.runtime_config.max_record_bytes,
        ) as store:
            try:
                commands = RuntimeTaskCommands(bundle.agent, store)
                await commands.replay_unfinished()
                loop = MathWorkerLoop(
                    agent=bundle.agent,
                    llm=bundle.llm,
                    config=bundle.config,
                    commands=commands,
                )
                await _run_loop(loop, logger)
            finally:
                clients_closed = True
                await _close_clients(bundle.agent, bundle.llm)
    finally:
        if not clients_closed:
            await _close_clients(bundle.agent, bundle.llm)
        logger.info("Math Worker Agent clients closed")


def main() -> None:
    """Sync entry point.

    Requires a worker profile name (config.yaml's ``workers:`` section).
    Fails loudly with a usage message rather than silently falling back
    to any implicit default.
    """
    if len(sys.argv) <= 1:
        sys.exit(
            "Usage: python -m math_worker <worker_profile_name>\n"
            "Worker profiles are defined in config.yaml's 'workers:' section."
        )
    worker_name = sys.argv[1]
    asyncio.run(_main_factory(worker_name))


if __name__ == "__main__":
    main()

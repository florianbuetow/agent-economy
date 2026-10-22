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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from service_commons.config import get_config_path as resolve_config_path

from base_agent.agent import BaseAgent
from base_agent.config import load_agent_config
from base_agent.runtime_config import load_runtime_state_config
from base_agent.runtime_intents import FileTaskIntentStore, RuntimeIntentError
from base_agent.runtime_postings import FileTaskPostingStore
from base_agent.runtime_task_commands import (
    RuntimeTaskCommands,
    log_runtime_replay,
    runtime_owner_from_agent,
)
from task_feeder.acceptance import AcceptanceLoop
from task_feeder.config import load_acceptance_settings, load_task_feeder_settings
from task_feeder.loop import TaskFeederLoop
from task_feeder.posting import TaskFeederPostingCoordinator
from task_feeder.reader import load_task_snapshot
from task_feeder.review import ReviewLoop

if TYPE_CHECKING:
    from task_feeder.config import AcceptanceConfig, TaskFeederConfig


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stderr,
    )


async def _create_account(agent: BaseAgent, logger: logging.Logger) -> None:
    # Create bank account (idempotent — 409 if already exists)
    try:
        await agent.create_account()
        logger.info("Bank account created for agent_id=%s", agent.agent_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            logger.info("Bank account already exists for agent_id=%s", agent.agent_id)
        else:
            raise


async def _run_loops(
    agent: BaseAgent,
    feeder_config: TaskFeederConfig,
    acceptance_config: AcceptanceConfig,
    commands: RuntimeTaskCommands,
    posting: TaskFeederPostingCoordinator,
    logger: logging.Logger,
) -> None:
    """Keep all feeder tasks inside the caller's identity-lease lifetime."""
    loop = TaskFeederLoop(agent, feeder_config, posting)
    loop.rebuild_task_map()
    acceptance = AcceptanceLoop(
        agent=agent,
        config=acceptance_config,
        now=lambda: datetime.now(UTC),
        commands=commands,
    )
    review = ReviewLoop(agent=agent, task_map=loop.task_map, commands=commands)

    # Graceful shutdown
    def _handle_signal() -> None:
        logger.info("Received shutdown signal")
        loop.stop()
        acceptance.stop()
        review.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, _handle_signal)

    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(loop.run())
        tasks.create_task(acceptance.run())
        tasks.create_task(review.run(feeder_config.review_interval_seconds))


async def _main() -> None:
    _setup_logging()
    logger = logging.getLogger("task_feeder")
    config_path = resolve_config_path(
        env_var_name="AGENT_CONFIG_PATH", default_filename="config.yaml"
    )
    feeder_config = load_task_feeder_settings(config_path)
    acceptance_config = load_acceptance_settings(config_path)
    agent_config = load_agent_config(feeder_config.handle, config_path)
    runtime_config = load_runtime_state_config(config_path)
    logger.info("Starting Task Feeder (handle=%s)", feeder_config.handle)
    logger.info("Tasks file: %s", feeder_config.tasks_file)

    agent = BaseAgent(agent_config)
    client_closed = False
    try:
        await agent.register()
        logger.info("Registered as agent_id=%s", agent.agent_id)
        owner = runtime_owner_from_agent(
            runtime_config=runtime_config,
            agent=agent,
            role="task-feeder",
            profile=feeder_config.handle,
            handle=feeder_config.handle,
        )
        with (
            FileTaskIntentStore(
                root_directory=runtime_config.directory,
                owner=owner,
                diagnostic_limit=runtime_config.diagnostic_limit,
                max_record_bytes=runtime_config.max_record_bytes,
            ) as store,
            FileTaskPostingStore.attach(
                store,
                max_source_bytes=feeder_config.source_max_bytes,
            ) as posting_store,
        ):
            try:
                posting = TaskFeederPostingCoordinator(agent, posting_store)
                await posting.replay_unfinished()
                posting.rebuild_task_map()

                source_path = Path(feeder_config.tasks_file)
                if not source_path.is_absolute():
                    source_path = (config_path.parent / source_path).resolve()
                try:
                    snapshot = load_task_snapshot(
                        source_path,
                        max_bytes=feeder_config.source_max_bytes,
                    )
                    posting.bind_current_source(snapshot, feeder_config)
                except RuntimeIntentError as exc:
                    if exc.reason != "source_conflict":
                        raise
                    logger.error("Fresh task feeding disabled: %s", exc)
                except (OSError, ValueError) as exc:
                    logger.error("Fresh task feeding disabled: %s", exc)

                commands = RuntimeTaskCommands(agent, store)
                replayed = await commands.replay_unfinished()
                log_runtime_replay(
                    replayed, logger=logger, diagnostic_limit=runtime_config.diagnostic_limit
                )
                await _create_account(agent, logger)
                await _run_loops(
                    agent,
                    feeder_config,
                    acceptance_config,
                    commands,
                    posting,
                    logger,
                )
            finally:
                client_closed = True
                await agent.close()
    finally:
        if not client_closed:
            await agent.close()
        logger.info("Task Feeder client closed")


def main() -> None:
    """Sync entry point."""
    asyncio.run(_main())


if __name__ == "__main__":
    main()

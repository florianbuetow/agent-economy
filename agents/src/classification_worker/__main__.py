"""Entry point for a classification worker.

Usage::

    cd agents/
    uv run python -m classification_worker classbot_keyword
    uv run python -m classification_worker classbot_llm

Profiles live in config.yaml's ``classification_workers:`` section.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import TYPE_CHECKING

import httpx
from service_commons.config import get_config_path as resolve_config_path

from base_agent.factory import AgentFactory
from base_agent.runtime_config import load_runtime_state_config
from base_agent.runtime_intents import FileTaskIntentStore
from base_agent.runtime_task_commands import (
    RuntimeTaskCommands,
    log_runtime_replay,
    runtime_owner_from_agent,
)
from base_agent.telemetry import configure_observability
from classification_worker.config import load_classification_profile
from classification_worker.loop import ClassificationWorkerLoop, LoopSettings
from classification_worker.solvers import KeywordSolver, LLMSolver
from math_worker.config import LLMConfig
from math_worker.llm_client import LLMClient

if TYPE_CHECKING:
    from classification_worker.config import ClassificationWorkerProfile
    from classification_worker.solvers import Solver

ROLE = "classification-worker"


def _build_solver(profile: ClassificationWorkerProfile) -> tuple[Solver, LLMClient | None]:
    if profile.strategy == "keyword":
        if profile.keywords is None:
            raise ValueError("keyword strategy requires keywords")
        return KeywordSolver(profile.keywords), None
    if profile.llm is None:
        raise ValueError("llm strategy requires an llm section")
    client = LLMClient(
        LLMConfig(
            base_url=profile.llm.base_url,
            api_key=profile.llm.api_key.get_secret_value(),
            model_id=profile.llm.model_id,
            temperature=profile.llm.temperature,
            max_tokens=profile.llm.max_tokens,
        )
    )
    return LLMSolver(client), client


async def _main(profile_name: str) -> None:
    configure_observability("classification-worker")
    logger = logging.getLogger("classification_worker")
    config_path = resolve_config_path(
        env_var_name="AGENT_CONFIG_PATH", default_filename="config.yaml"
    )
    profile = load_classification_profile(config_path, profile_name)
    runtime_config = load_runtime_state_config(config_path)
    agent = AgentFactory(config_path=config_path).create_agent(profile.handle)
    solver, llm = _build_solver(profile)
    try:
        await agent.register()
        logger.info("Registered %s as agent_id=%s", profile.handle, agent.agent_id)
        owner = runtime_owner_from_agent(
            runtime_config=runtime_config,
            agent=agent,
            role=ROLE,
            profile=profile_name,
            handle=profile.handle,
        )
        with FileTaskIntentStore(
            root_directory=runtime_config.directory,
            owner=owner,
            diagnostic_limit=runtime_config.diagnostic_limit,
            max_record_bytes=runtime_config.max_record_bytes,
        ) as store:
            try:
                await agent.create_account()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 409:
                    raise
            commands = RuntimeTaskCommands(agent, store)
            replayed = await commands.replay_unfinished()
            log_runtime_replay(
                replayed, logger=logger, diagnostic_limit=runtime_config.diagnostic_limit
            )
            behavior = profile.behavior
            loop = ClassificationWorkerLoop(
                agent=agent,
                solver=solver,
                settings=LoopSettings(
                    handle=profile.handle,
                    scan_interval_seconds=behavior.scan_interval_seconds,
                    poll_interval_seconds=behavior.poll_interval_seconds,
                    max_poll_attempts=behavior.max_poll_attempts,
                    error_backoff_seconds=behavior.error_backoff_seconds,
                    min_reward=behavior.min_reward,
                    max_reward=behavior.max_reward,
                    pricing=profile.pricing.to_rule(),
                ),
                commands=commands,
            )
            for sig in (signal.SIGINT, signal.SIGTERM):
                asyncio.get_running_loop().add_signal_handler(sig, loop.stop)
            await loop.run()
    finally:
        try:
            if llm is not None:
                await llm.close()
        finally:
            await agent.close()


def main() -> None:
    """A profile name is required; nothing falls back to an implicit profile."""
    if len(sys.argv) != 2:
        sys.exit("Usage: python -m classification_worker <classification_worker_profile>")
    asyncio.run(_main(sys.argv[1]))


if __name__ == "__main__":
    main()

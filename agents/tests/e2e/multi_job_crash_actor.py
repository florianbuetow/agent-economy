"""One-cycle math worker subprocess with an optional post-upload crash gate."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from base_agent.agent import BaseAgent
from base_agent.config import load_agent_config
from base_agent.runtime_config import load_runtime_state_config
from base_agent.runtime_intents import FileTaskIntentStore
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent
from math_worker.config import MathWorkerConfig
from math_worker.loop import MathWorkerLoop


class _LLM:
    async def complete(self, _system: str, _prompt: str) -> Any:
        return SimpleNamespace(content="ANSWER: recovered output")


async def _run(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    agent = BaseAgent(load_agent_config("alice", config_path))
    await agent.register()
    runtime = load_runtime_state_config(config_path)
    owner = runtime_owner_from_agent(
        runtime_config=runtime,
        agent=agent,
        role="math-worker",
        profile="adversarial_multi_job",
        handle="alice",
    )
    original_upload = agent.upload_asset
    if args.crash_task:

        async def upload_then_wait(task_id: str, filename: str, content: bytes) -> dict[str, Any]:
            result = await original_upload(task_id, filename, content)
            if task_id == args.crash_task:
                Path(args.marker).write_text(str(result["asset_id"]), encoding="utf-8")
                await asyncio.Event().wait()
            return result

        agent.upload_asset = upload_then_wait  # type: ignore[method-assign]
    config = MathWorkerConfig(
        handle="alice",
        scan_interval_seconds=0,
        poll_interval_seconds=0,
        max_poll_attempts=1,
        error_backoff_seconds=0,
        min_reward=1,
        max_reward=1_000,
    )
    try:
        with FileTaskIntentStore(
            root_directory=runtime.directory,
            owner=owner,
            diagnostic_limit=runtime.diagnostic_limit,
            max_record_bytes=runtime.max_record_bytes,
        ) as store:
            loop = MathWorkerLoop(agent, _LLM(), config, RuntimeTaskCommands(agent, store))
            await loop._cycle()
    finally:
        await agent.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--crash-task")
    parser.add_argument("--marker", default="")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()

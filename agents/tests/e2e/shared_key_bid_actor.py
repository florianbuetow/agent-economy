"""Subprocess actor used by the shared-key adversarial identity test."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from service_auth.factory import AgentFactory


async def _run(args: argparse.Namespace) -> None:
    factory = AgentFactory(config_path=Path(args.config))
    agent = factory.create_agent("alice")
    try:
        registration = await agent.register()
        Path(args.ready).write_text(str(registration["agent_id"]), encoding="utf-8")
        release = Path(args.release)
        for _attempt in range(1_000):
            if release.exists():
                break
            await asyncio.sleep(0.01)
        else:
            raise TimeoutError("shared-key actor release barrier did not open")
        try:
            response = await agent.submit_bid(
                task_id=args.task_id,
                amount=args.amount,
                operation_id=args.operation_id,
                expected_lifecycle_version=0,
            )
            result = {
                "agent_id": registration["agent_id"],
                "status": response.status_code,
                "body": response.body,
            }
        except httpx.HTTPStatusError as exc:
            result = {
                "agent_id": registration["agent_id"],
                "status": exc.response.status_code,
                "body": exc.response.json(),
            }
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        await agent.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--amount", required=True, type=int)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--release", required=True)
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()

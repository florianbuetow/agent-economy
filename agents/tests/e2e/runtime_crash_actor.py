"""Child process used by the MP-07 runtime crash/replay acceptance proof."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from base_agent.agent import BaseAgent
from base_agent.config import load_agent_config
from base_agent.runtime_config import load_runtime_state_config
from base_agent.runtime_intent_models import NewTaskIntent
from base_agent.runtime_intents import FileTaskIntentStore
from base_agent.runtime_task_commands import RuntimeTaskCommands, runtime_owner_from_agent

if TYPE_CHECKING:
    from service_auth import TaskBoardCommandResponse


def _write_durable_json(path: Path, value: object) -> None:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with staged.open("w", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    staged.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected an object in {path}")
    return value


async def _agent_and_commands(
    spec: dict[str, Any],
) -> tuple[BaseAgent, FileTaskIntentStore, RuntimeTaskCommands]:
    config_path = Path(spec["agent_config_path"])
    agent = BaseAgent(load_agent_config(spec["handle"], config_path))
    registration = await agent.register()
    if registration["agent_id"] != spec["registered_agent_id"]:
        raise RuntimeError("Persisted identity did not reopen as the registered actor")
    runtime_config = load_runtime_state_config(config_path)
    owner = runtime_owner_from_agent(
        runtime_config=runtime_config,
        agent=agent,
        role=spec["role"],
        profile=spec["profile"],
        handle=spec["handle"],
    )
    store = FileTaskIntentStore(
        root_directory=runtime_config.directory,
        owner=owner,
        diagnostic_limit=runtime_config.diagnostic_limit,
        max_record_bytes=runtime_config.max_record_bytes,
    )
    store.__enter__()
    return agent, store, RuntimeTaskCommands(agent, store)


async def _crash(spec: dict[str, Any], ready_path: Path, gate_path: Path) -> None:
    _write_durable_json(ready_path, {"pid": os.getpid(), "mode": "crash_starting"})
    agent, store, commands = await _agent_and_commands(spec)
    try:
        intent = NewTaskIntent(**spec["intent"])
        method_name = {
            "submit_bid": "submit_bid",
            "accept_bid": "accept_bid",
            "submit_deliverable": "submit_deliverable",
            "approve_task": "approve_task",
            "dispute_task": "dispute_task",
        }[intent.action]
        original = getattr(agent, method_name)

        async def gated(*args: object, **kwargs: object) -> TaskBoardCommandResponse:
            response: TaskBoardCommandResponse | None = None
            if spec["crash_stage"] == "after_response":
                call = original
                response = await call(*args, **kwargs)
            gate: dict[str, object] = {
                "pid": os.getpid(),
                "action": intent.action,
                "crash_stage": spec["crash_stage"],
                "operation_id": intent.operation_id,
            }
            if response is not None:
                gate["response"] = {"status_code": response.status_code, "body": response.body}
            _write_durable_json(gate_path, gate)
            await asyncio.Event().wait()
            raise AssertionError("unreachable gate released")

        setattr(agent, method_name, gated)

        async def prepare() -> NewTaskIntent:
            return intent

        await commands.execute(task_id=intent.task_id, slot=intent.slot, prepare=prepare)
        raise AssertionError("crash actor returned without being killed")
    finally:
        store.__exit__(None, None, None)
        await agent.close()


async def _replay(spec: dict[str, Any], ready_path: Path, result_path: Path) -> None:
    _write_durable_json(ready_path, {"pid": os.getpid(), "mode": "replay_starting"})
    agent, store, commands = await _agent_and_commands(spec)
    try:
        results = await commands.replay_unfinished()
        _write_durable_json(
            result_path,
            {
                "pid": os.getpid(),
                "mode": "replay_unfinished_only",
                "results": [result.as_json() for result in results],
            },
        )
    finally:
        store.__exit__(None, None, None)
        await agent.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("crash", "replay"))
    parser.add_argument("--spec", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ready", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    spec = _load_object(arguments.spec)
    if arguments.mode == "crash":
        asyncio.run(_crash(spec, arguments.ready, arguments.output))
    else:
        asyncio.run(_replay(spec, arguments.ready, arguments.output))


if __name__ == "__main__":
    main()

"""Test-only crash gates around demo replay native task mutations."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any

from demo_replay import clients  # type: ignore[import-untyped]


METHODS = {
    "bid": "submit_bid",
    "accept_bid": "accept_bid",
    "submit_deliverable": "submit_deliverable",
    "approve": "approve_task",
    "dispute": "dispute_task",
}


def _write_gate(path: Path, value: object) -> None:
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    staged.replace(path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _arguments(action: str, positional: tuple[Any, ...]) -> dict[str, object]:
    if action == "bid":
        return {"amount": positional[3]}
    if action == "accept_bid":
        return {"bid_id": positional[3]}
    if action == "dispute":
        return {"reason": positional[3]}
    return {}


def main() -> None:
    """Install one method gate, then execute the production CLI unchanged."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=tuple(METHODS), required=True)
    parser.add_argument(
        "--phase", choices=("before_send", "after_response"), required=True
    )
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("demo_arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if not arguments.demo_arguments or arguments.demo_arguments[0] != "--":
        raise ValueError("Production demo arguments must follow --")

    method_name = METHODS[arguments.action]
    original = getattr(clients, method_name)

    async def gated(*positional: Any, **keywords: Any) -> dict[str, Any]:
        response = None
        if arguments.phase == "after_response":
            response = await original(*positional, **keywords)
            if not isinstance(response, dict):
                raise AssertionError("Expected a real native JSON response before crash gate")
        actor = positional[1]
        gate = {
            "pid": os.getpid(),
            "pgid": os.getpgrp(),
            "sid": os.getsid(0),
            "phase": arguments.phase,
            "action": arguments.action,
            "method": method_name,
            "actor_handle": actor.handle,
            "actor_id": actor.agent_id,
            "actor_public_key": actor.public_key_string(),
            "task_id": positional[2],
            "operation_id": keywords["operation_id"],
            "expected_lifecycle_version": keywords["expected_lifecycle_version"],
            "arguments": _arguments(arguments.action, positional),
            "response": response,
        }
        _write_gate(arguments.gate, gate)
        await asyncio.Event().wait()
        raise AssertionError("Crash-gated demo CLI returned without process death")

    setattr(clients, method_name, gated)
    sys.argv = ["demo_replay", *arguments.demo_arguments[1:]]
    runpy.run_module("demo_replay", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()

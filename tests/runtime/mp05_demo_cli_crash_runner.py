"""Test-only crash gates around the demo CLI's real native posting transport."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from service_auth.mixins.task_board import TaskBoardMixin

if TYPE_CHECKING:
    from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse


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


def main() -> None:
    """Install one transport gate, then execute the production CLI unchanged."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=("before_send", "after_response"), required=True
    )
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("demo_arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if not arguments.demo_arguments or arguments.demo_arguments[0] != "--":
        raise ValueError("Production demo arguments must follow --")

    original = TaskBoardMixin.submit_task_posting

    async def gated(self: Any, intent: TaskPostingIntent) -> TaskPostingResponse:
        response = None
        if arguments.phase == "after_response":
            response = await original(self, intent)
            if (
                response.status_code != 201
                or response.body.get("publication_state") != "published"
            ):
                raise AssertionError(
                    "Expected a real HTTP 201 published response before the crash gate"
                )
        gate = {
            "pid": os.getpid(),
            "pgid": os.getpgrp(),
            "sid": os.getsid(0),
            "phase": arguments.phase,
            "actor_id": self.agent_id,
            "intent": intent.as_json(),
            "response": (
                None
                if response is None
                else {"status_code": response.status_code, "body": response.body}
            ),
        }
        _write_gate(arguments.gate, gate)
        await asyncio.Event().wait()
        raise AssertionError("Crash-gated demo CLI returned without process death")

    setattr(TaskBoardMixin, "submit_task_posting", gated)
    sys.argv = ["demo_replay", *arguments.demo_arguments[1:]]
    runpy.run_module("demo_replay", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()

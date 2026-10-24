"""Test-only send gates around the real Task Feeder entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
from pathlib import Path
from typing import TYPE_CHECKING

from base_agent.agent import BaseAgent

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
    """Install a return-seam gate, then execute the production CLI unchanged."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before_send", "after_response"), required=True)
    parser.add_argument("--gate", type=Path, required=True)
    args = parser.parse_args()
    original = BaseAgent.submit_task_posting

    async def gated(self: BaseAgent, intent: TaskPostingIntent) -> TaskPostingResponse:
        response = None
        if args.phase == "after_response":
            response = await original(self, intent)
            if response.status_code != 201:
                raise AssertionError(f"Expected real HTTP 201, got {response.status_code}")
        gate = {
            "pid": os.getpid(),
            "pgid": os.getpgrp(),
            "sid": os.getsid(0),
            "phase": args.phase,
            "actor_id": self.agent_id,
            "operation_id": intent.operation_id,
            "task_id": intent.task_id,
            "lock_operation_id": intent.lock_operation_id,
            "request_sha256": intent.request_sha256,
            "intent": intent.as_json(),
            "response": (
                None
                if response is None
                else {"status_code": response.status_code, "body": response.body}
            ),
        }
        _write_gate(args.gate, gate)
        await asyncio.Event().wait()
        raise AssertionError("A crash gate must end only through process death")

    setattr(BaseAgent, "submit_task_posting", gated)  # noqa: B010 - test-only method injection
    runpy.run_module("task_feeder", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()

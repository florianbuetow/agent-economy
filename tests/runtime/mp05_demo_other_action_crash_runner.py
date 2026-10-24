"""Test-only process gates for MP-05 demo actions outside native task commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

from demo_replay import clients  # type: ignore[import-untyped]


METHODS = {
    "upload_asset": "upload_asset",
    "feedback": "submit_feedback",
    "file_claim": "file_claim",
    "submit_rebuttal": "submit_rebuttal",
    "trigger_ruling": "trigger_ruling",
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


def main() -> None:
    """Patch one client call, then execute the production module unchanged."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--action", choices=tuple(METHODS), required=True)
    parser.add_argument(
        "--phase",
        choices=("before_send", "after_response", "forbid_send"),
        required=True,
    )
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("demo_arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if not arguments.demo_arguments or arguments.demo_arguments[0] != "--":
        raise ValueError("Production demo arguments must follow --")

    method_name = METHODS[arguments.action]
    original: Callable[..., Awaitable[dict[str, Any]]] = getattr(clients, method_name)

    async def gated(*positional: Any, **keywords: Any) -> dict[str, Any]:
        response = None
        if arguments.phase == "after_response":
            response = await original(*positional, **keywords)
            if not isinstance(response, dict):
                raise AssertionError("Expected a real JSON response before the crash gate")
        _write_gate(
            arguments.gate,
            {
                "pid": os.getpid(),
                "pgid": os.getpgrp(),
                "sid": os.getsid(0),
                "action": arguments.action,
                "phase": arguments.phase,
                "method": method_name,
                "response": response,
            },
        )
        if arguments.phase == "forbid_send":
            raise AssertionError("Restart attempted a forbidden target resend")
        await asyncio.Event().wait()
        raise AssertionError("Crash-gated demo CLI returned without process death")

    setattr(clients, method_name, gated)
    sys.argv = ["demo_replay", *arguments.demo_arguments[1:]]
    runpy.run_module("demo_replay", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()

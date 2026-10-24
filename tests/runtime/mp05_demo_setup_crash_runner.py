"""Test-only crash gates around real demo registration and funding clients."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any, Callable

from demo_replay import clients


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


async def _block_at_gate(path: Path, evidence: dict[str, object]) -> None:
    _write_gate(
        path,
        {
            "pid": os.getpid(),
            "pgid": os.getpgrp(),
            "sid": os.getsid(0),
            **evidence,
        },
    )
    await asyncio.Event().wait()
    raise AssertionError("Setup crash gate returned without process death")


def _install_registration_gate(path: Path, phase: str) -> None:
    original = clients.register_agent

    async def gated(client: Any, agent: Any, identity_url: str) -> dict[str, Any]:
        if agent.handle != "poster":
            return await original(client, agent, identity_url)
        request = {
            "handle": agent.handle,
            "name": agent.name,
            "public_key": agent.public_key_string(),
            "identity_url": identity_url,
        }
        response = None
        if phase == "after_response":
            response = await original(client, agent, identity_url)
        await _block_at_gate(
            path,
            {
                "action": "register",
                "phase": phase,
                "request": request,
                "response": response,
                "actor_id_after_response": agent.agent_id,
            },
        )
        raise AssertionError("unreachable")

    clients.register_agent = gated


def _install_account_gate(path: Path, phase: str) -> None:
    original = clients.create_account

    async def gated(
        client: Any, platform: Any, agent_id: str, bank_url: str
    ) -> dict[str, object]:
        request = {
            "agent_id": agent_id,
            "initial_balance": 0,
            "bank_url": bank_url,
        }
        response = None
        if phase == "after_response":
            response = await original(client, platform, agent_id, bank_url)
        await _block_at_gate(
            path,
            {
                "action": "create_account",
                "phase": phase,
                "request": request,
                "response": response,
            },
        )
        raise AssertionError("unreachable")

    clients.create_account = gated


def _install_funding_gate(path: Path, phase: str) -> None:
    original = clients.credit_account

    async def gated(
        client: Any,
        platform: Any,
        account_id: str,
        amount: int,
        bank_url: str,
        *,
        operation_id: str,
        reference: str,
    ) -> dict[str, object]:
        request = {
            "account_id": account_id,
            "amount": amount,
            "bank_url": bank_url,
            "operation_id": operation_id,
            "reference": reference,
        }
        response = None
        if phase == "after_response":
            response = await original(
                client,
                platform,
                account_id,
                amount,
                bank_url,
                operation_id=operation_id,
                reference=reference,
            )
        await _block_at_gate(
            path,
            {
                "action": "fund",
                "phase": phase,
                "request": request,
                "response": response,
            },
        )
        raise AssertionError("unreachable")

    clients.credit_account = gated


def main() -> None:
    """Install one setup-client gate, then execute the production CLI unchanged."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action", choices=("register", "create_account", "fund"), required=True
    )
    parser.add_argument(
        "--phase", choices=("before_send", "after_response"), required=True
    )
    parser.add_argument("--gate", type=Path, required=True)
    parser.add_argument("demo_arguments", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if not arguments.demo_arguments or arguments.demo_arguments[0] != "--":
        raise ValueError("Production demo arguments must follow --")

    installers: dict[str, Callable[[Path, str], None]] = {
        "register": _install_registration_gate,
        "create_account": _install_account_gate,
        "fund": _install_funding_gate,
    }
    installers[arguments.action](arguments.gate, arguments.phase)
    sys.argv = ["demo_replay", *arguments.demo_arguments[1:]]
    runpy.run_module("demo_replay", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()

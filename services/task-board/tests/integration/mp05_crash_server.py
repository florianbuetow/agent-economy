"""Owned Task Board server with an MP-05 crash gate after real Bank lock commit."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from service_auth import parse_jws
from service_clients.bank import BankClient

from task_board_service.app import create_app
from task_board_service.services.task_db_client import TaskDbClient

if TYPE_CHECKING:
    from service_auth.task_postings import TaskPostingIntent, TaskPostingResponse


def _write_durable_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise


def _install_lock_commit_gate(gate_path: Path) -> None:
    original = BankClient.escrow_lock
    original_reconcile = TaskDbClient.reconcile_task_publication
    fired = False
    reconciliation_lock = threading.Lock()
    dispatch_started = False
    never_release = threading.Event()

    def gated_reconcile(
        self: TaskDbClient,
        operation_id: str,
        intent: TaskPostingIntent | None,
    ) -> TaskPostingResponse:
        with reconciliation_lock:
            if not dispatch_started:
                return original_reconcile(self, operation_id, intent)
        never_release.wait()
        raise AssertionError("Crash publication barrier released without SIGKILL")

    def fence_publication() -> None:
        nonlocal dispatch_started
        # Drain any earlier reconciliation before the real Bank dispatch. Every
        # subsequent publisher in this crash process then waits for process death.
        with reconciliation_lock:
            dispatch_started = True

    async def _locked_then_blocked(self: BankClient, escrow_token: str) -> dict[str, Any]:
        nonlocal fired
        await asyncio.to_thread(fence_publication)
        receipt = await original(self, escrow_token)
        if fired:
            raise RuntimeError("MP-05 crash gate received an unexpected second lock")
        parsed = parse_jws(escrow_token)
        fired = True
        _write_durable_json(
            gate_path,
            {
                "pid": os.getpid(),
                "task_id": parsed.payload["task_id"],
                "poster_id": parsed.payload["agent_id"],
                "amount": parsed.payload["amount"],
                "lock_operation_id": parsed.payload["operation_id"],
                "receipt": receipt,
            },
        )
        await asyncio.Event().wait()
        raise AssertionError("MP-05 crash gate was released without SIGKILL")

    BankClient.escrow_lock = _locked_then_blocked  # type: ignore[method-assign]
    TaskDbClient.reconcile_task_publication = gated_reconcile  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("crash", "normal"))
    parser.add_argument("--fd", required=True, type=int)
    parser.add_argument("--gate", type=Path)
    arguments = parser.parse_args()
    if arguments.fd < 0:
        raise ValueError("Inherited server file descriptor must be nonnegative")
    if arguments.mode == "crash":
        if arguments.gate is None:
            raise ValueError("Crash mode requires an explicit gate path")
        _install_lock_commit_gate(arguments.gate)
    elif arguments.gate is not None:
        raise ValueError("Normal mode must not receive a crash gate")

    uvicorn.run(create_app(), fd=arguments.fd, log_level="warning")


if __name__ == "__main__":
    main()

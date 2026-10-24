"""Owned Task Board server with deterministic legacy-refund crash gates."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any

import uvicorn
from service_clients.bank import BankClient
from task_board_service.app import create_app
from task_board_service.services.task_db_client import TaskDbClient


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


async def _block_after_gate(gate_path: Path, phase: str, evidence: dict[str, Any]) -> None:
    _write_durable_json(
        gate_path,
        {"pid": os.getpid(), "phase": phase, "evidence": evidence},
    )
    await asyncio.Event().wait()
    raise AssertionError("Legacy-refund crash gate released without SIGKILL")


def _install_refund_intent_gate(gate_path: Path) -> None:
    original = TaskDbClient.reconcile_legacy_posting_observation
    fired = False

    def reconciled_then_blocked(
        self: TaskDbClient,
        observation_id: str,
    ) -> dict[str, Any]:
        nonlocal fired
        envelope = original(self, observation_id)
        if not fired and envelope["observation_state"] == "refund_pending":
            fired = True
            _write_durable_json(
                gate_path,
                {
                    "pid": os.getpid(),
                    "phase": "refund_intent",
                    "evidence": envelope,
                },
            )
            # This runs in PostingRecovery's owned worker thread. Blocking here
            # keeps the process alive at the exact pre-Bank boundary.
            threading.Event().wait()
        return envelope

    TaskDbClient.reconcile_legacy_posting_observation = reconciled_then_blocked  # type: ignore[method-assign]


def _install_refund_commit_gate(gate_path: Path) -> None:
    original = BankClient.escrow_release
    fired = False

    async def committed_then_blocked(
        self: BankClient,
        escrow_id: str,
        recipient_account_id: str,
        *,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        nonlocal fired
        result = await original(
            self,
            escrow_id=escrow_id,
            recipient_account_id=recipient_account_id,
            operation_id=operation_id,
            source_reference=source_reference,
        )
        if not fired:
            fired = True
            await _block_after_gate(
                gate_path,
                "refund_commit",
                {
                    "escrow_id": escrow_id,
                    "recipient_account_id": recipient_account_id,
                    "operation_id": operation_id,
                    "source_reference": source_reference,
                },
            )
        return result

    BankClient.escrow_release = committed_then_blocked  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True, choices=("refund-intent", "refund-commit"))
    parser.add_argument("--fd", required=True, type=int)
    parser.add_argument("--gate", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.fd < 0:
        raise ValueError("Inherited server file descriptor must be nonnegative")
    if arguments.phase == "refund-intent":
        _install_refund_intent_gate(arguments.gate)
    else:
        _install_refund_commit_gate(arguments.gate)
    uvicorn.run(create_app(), fd=arguments.fd, log_level="warning")


if __name__ == "__main__":
    main()

"""Owned Task Board server with gates at real MP-05 publication phase commits."""

from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn

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


def _block_after_gate(gate_path: Path, phase: str, response: TaskPostingResponse) -> None:
    _write_durable_json(
        gate_path,
        {
            "pid": os.getpid(),
            "phase": phase,
            "status_code": response.status_code,
            "publication": response.body,
        },
    )
    threading.Event().wait()
    raise AssertionError("MP-05 publication phase gate released without SIGKILL")


def _install_admission_commit_gate(gate_path: Path) -> None:
    original = TaskDbClient.admit_task_publication
    fired = False

    def admitted_then_blocked(
        self: TaskDbClient,
        intent: TaskPostingIntent,
    ) -> TaskPostingResponse:
        nonlocal fired
        response = original(self, intent)
        if not fired:
            fired = True
            _block_after_gate(gate_path, "admission_committed", response)
        return response

    TaskDbClient.admit_task_publication = admitted_then_blocked  # type: ignore[method-assign]


def _install_publication_commit_gate(gate_path: Path) -> None:
    original = TaskDbClient.reconcile_task_publication
    fired = False

    def published_then_blocked(
        self: TaskDbClient,
        operation_id: str,
        intent: TaskPostingIntent | None,
    ) -> TaskPostingResponse:
        nonlocal fired
        response = original(self, operation_id, intent)
        if not fired and response.body["publication_state"] == "published":
            fired = True
            _block_after_gate(gate_path, "publication_committed", response)
        return response

    TaskDbClient.reconcile_task_publication = published_then_blocked  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        required=True,
        choices=("admission-commit", "publication-commit", "normal"),
    )
    parser.add_argument("--fd", required=True, type=int)
    parser.add_argument("--gate", type=Path)
    arguments = parser.parse_args()
    if arguments.fd < 0:
        raise ValueError("Inherited server file descriptor must be nonnegative")
    if arguments.mode == "normal":
        if arguments.gate is not None:
            raise ValueError("Normal mode must not receive a crash gate")
    elif arguments.gate is None:
        raise ValueError("Crash modes require an explicit gate path")
    elif arguments.mode == "admission-commit":
        _install_admission_commit_gate(arguments.gate)
    else:
        _install_publication_commit_gate(arguments.gate)

    uvicorn.run(create_app(), fd=arguments.fd, log_level="warning")


if __name__ == "__main__":
    main()

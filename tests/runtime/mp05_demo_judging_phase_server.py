"""Test-only Court factory that gates after authoritative ``judging`` persistence."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from court_service.app import create_app as production_create_app
from court_service.services.dispute_service import DisputeService


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


def create_app() -> Any:
    """Install the ruling phase gate before constructing the production app."""
    raw_gate = os.environ.get("MP05_JUDGING_GATE")
    if raw_gate is None or raw_gate == "":
        raise RuntimeError("MP05_JUDGING_GATE must identify an owned evidence path")
    gate_path = Path(raw_gate)
    original = DisputeService.begin_ruling

    def gated(self: DisputeService, dispute_id: str) -> dict[str, Any]:
        dispute = original(self, dispute_id)
        _write_gate(
            gate_path,
            {
                "pid": os.getpid(),
                "pgid": os.getpgrp(),
                "sid": os.getsid(0),
                "dispute_id": dispute_id,
                "task_id": dispute["task_id"],
                "phase": "after_judging_persisted",
            },
        )
        threading.Event().wait()
        raise AssertionError("Judging gate returned without Court process death")

    DisputeService.begin_ruling = gated
    return production_create_app()

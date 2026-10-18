"""Small test-only primitives for the native Task Board command contract."""

from __future__ import annotations

import uuid
from typing import Any


def new_operation_id() -> str:
    """Allocate an explicit caller-owned operation identity for one test command."""
    return f"op-{uuid.uuid4()}"


def lifecycle_version(task: dict[str, Any]) -> int:
    """Read and validate the task's native optimistic-concurrency version."""
    value = task.get("lifecycle_version")
    assert type(value) is int and value >= 0
    return value

"""JSONL task file reader."""

from __future__ import annotations

import json
import logging
import os
import random
import stat
from pathlib import Path
from typing import TYPE_CHECKING

from service_auth.posting_journal import (
    RawTask,
    TaskSourceEntry,
    TaskSourceSnapshot,
    parse_source_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "RawTask",
    "TaskSourceEntry",
    "TaskSourceSnapshot",
    "iterate_tasks",
    "load_task_snapshot",
    "load_tasks",
]


def _source_path(value: object) -> Path:
    if not isinstance(value, Path) or not value.is_absolute():
        raise ValueError("Tasks source path must be explicit and absolute")
    return value.parent.resolve(strict=True) / value.name


def load_task_snapshot(path: Path, *, max_bytes: int) -> TaskSourceSnapshot:
    """Read one bounded stable source file before constructing immutable evidence."""
    source_path = _source_path(path)
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("Source byte limit must be a positive integer")
    descriptor = os.open(source_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Tasks source must be a regular file")
        if before.st_size > max_bytes:
            raise ValueError("Tasks source exceeds the configured byte limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        source_bytes = b"".join(chunks)
        if len(source_bytes) > max_bytes:
            raise ValueError("Tasks source exceeds the configured byte limit")
        after = os.fstat(descriptor)
        try:
            current = source_path.stat(follow_symlinks=False)
        except OSError as exc:
            raise ValueError("Tasks source changed while reading") from exc
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(source_bytes) != before.st_size or any(
            getattr(before, field) != getattr(after, field)
            or getattr(after, field) != getattr(current, field)
            for field in fields
        ):
            raise ValueError("Tasks source changed while reading")
    finally:
        os.close(descriptor)
    return parse_source_bytes(source_bytes, source_path=source_path, max_bytes=max_bytes)


def load_tasks(path: Path) -> list[RawTask]:
    """Load all tasks from a JSONL file.

    Each line must be a JSON object with at least: title, spec, solutions, level, problem_type.

    Args:
        path: Path to the ``.jsonl`` file.

    Returns:
        List of RawTask objects.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If a line cannot be parsed.
    """
    if not path.exists():
        msg = f"Tasks file not found: {path}"
        raise FileNotFoundError(msg)

    tasks: list[RawTask] = []
    with path.open() as fh:
        for line_num, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                msg = f"Invalid JSON on line {line_num}: {exc}"
                raise ValueError(msg) from exc

            tasks.append(
                RawTask(
                    title=obj["title"],
                    spec=obj["spec"],
                    solutions=tuple(obj["solutions"]),
                    level=obj["level"],
                    problem_type=obj["problem_type"],
                    solution_note=obj.get("solution_note"),
                )
            )

    logger.info("Loaded %d tasks from %s", len(tasks), path)
    return tasks


def iterate_tasks(
    tasks: list[RawTask],
    *,
    shuffle: bool,
) -> Iterator[RawTask]:
    """Yield tasks in order or shuffled, looping forever.

    Args:
        tasks:   The full task list.
        shuffle: Whether to shuffle before each pass.

    Yields:
        RawTask objects, cycling indefinitely.
    """
    if not tasks:
        return

    pool = list(tasks)
    while True:
        if shuffle:
            random.shuffle(pool)
        yield from pool

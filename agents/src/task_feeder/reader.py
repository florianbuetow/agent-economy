"""JSONL task file reader."""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RawTask:
    """One task entry read from the JSONL file."""

    title: str
    spec: str
    solutions: list[str]
    level: int
    problem_type: str
    solution_note: str | None


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
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
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
                    solutions=obj["solutions"],
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

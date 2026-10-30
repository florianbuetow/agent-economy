"""Render a structured classification seed into the feeder's JSONL task source.

Usage::

    cd agents/
    uv run python -m task_feeder.workloads <seed.json> <out.jsonl>
"""

from __future__ import annotations

import sys
from pathlib import Path

from task_feeder.workloads.classification import load_seed, write_task_source


def main() -> None:
    """Both paths are required; nothing is assumed."""
    if len(sys.argv) != 3:
        sys.exit("Usage: python -m task_feeder.workloads <seed.json> <out.jsonl>")
    tasks = load_seed(Path(sys.argv[1]))
    out = Path(sys.argv[2])
    write_task_source(tasks, out)
    print(f"Wrote {len(tasks)} classification tasks to {out}")


if __name__ == "__main__":
    main()

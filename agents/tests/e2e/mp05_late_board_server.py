"""Owned real Task Board server that reports its exact actor PID before serving."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import uvicorn
from task_board_service.app import create_app


def _write_ready(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid()}, handle, sort_keys=True, separators=(",", ":"))
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fd", required=True, type=int)
    parser.add_argument("--ready", required=True, type=Path)
    arguments = parser.parse_args()
    if arguments.fd < 0:
        raise ValueError("Inherited server file descriptor must be nonnegative")
    _write_ready(arguments.ready)
    uvicorn.run(create_app(), fd=arguments.fd, log_level="warning")


if __name__ == "__main__":
    main()

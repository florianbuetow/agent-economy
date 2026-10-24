"""Real DB Gateway server with an owned file-backed business clock for MP-08 tests."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import uvicorn

from db_gateway_service import task_time
from db_gateway_service.app import create_app


def _clock_reader(path: Path) -> int:
    raw = path.read_text(encoding="ascii")
    if not raw.endswith("\n") or raw.count("\n") != 1:
        raise RuntimeError("MP-08 Gateway clock file must contain one newline-terminated integer")
    value = raw[:-1]
    if not value.isascii() or not value.isdigit():
        raise RuntimeError("MP-08 Gateway clock file must contain decimal microseconds")
    clock_us = int(value)
    if not 0 <= clock_us <= 253_402_300_799_999_999:
        raise RuntimeError("MP-08 Gateway clock is outside the supported epoch range")
    return clock_us


async def _serve(arguments: argparse.Namespace) -> None:
    task_time.utc_now_us = lambda: _clock_reader(arguments.clock)  # type: ignore[assignment]
    server = uvicorn.Server(
        uvicorn.Config(create_app(), fd=arguments.fd, log_level="warning")
    )
    await server.serve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock", type=Path, required=True)
    parser.add_argument("--fd", type=int, required=True)
    arguments = parser.parse_args()
    if arguments.fd < 0:
        raise ValueError("An owned inherited listener is required")
    _clock_reader(arguments.clock)
    asyncio.run(_serve(arguments))


if __name__ == "__main__":
    main()

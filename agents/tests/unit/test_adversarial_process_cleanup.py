"""Adversarial subprocess helpers always reap owned process groups."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import TYPE_CHECKING

import pytest

from tests.e2e.test_adversarial_multi_job_process_restart import _cleanup as cleanup_restart
from tests.e2e.test_adversarial_shared_key_processes import _cleanup as cleanup_shared

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("cleanup", [cleanup_shared, cleanup_restart])
async def test_cleanup_kills_and_reaps_owned_process_group(
    cleanup: Callable[[list[asyncio.subprocess.Process]], Awaitable[None]],
) -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        start_new_session=True,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert os.getpgid(process.pid) == process.pid

    await cleanup([process])

    assert process.returncode == -signal.SIGKILL
    with pytest.raises(ProcessLookupError):
        os.getpgid(process.pid)

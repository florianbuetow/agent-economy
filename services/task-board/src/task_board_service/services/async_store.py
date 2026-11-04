"""Run synchronous Gateway storage calls without blocking Task Board's loop."""

from __future__ import annotations

import asyncio
import contextlib
from contextvars import copy_context
from functools import partial
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from concurrent.futures import Executor


async def store_call[T](
    callback: Callable[..., T],
    /,
    *args: Any,
    executor: Executor | None,
    **kwargs: Any,
) -> T:
    """Execute synchronous storage I/O off-loop and drain it on cancellation.

    A cancelled request must not leave an unobserved mutation running in a
    worker thread. Drain the operation before propagating cancellation.
    """
    context = copy_context()
    future = asyncio.get_running_loop().run_in_executor(
        executor,
        partial(context.run, callback, *args, **kwargs),
    )
    task = asyncio.ensure_future(future)
    try:
        return cast("T", await asyncio.shield(task))
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await task
        raise


async def store_read[T](callback: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Execute one independent storage read on the default worker pool."""
    return await store_call(callback, *args, executor=None, **kwargs)

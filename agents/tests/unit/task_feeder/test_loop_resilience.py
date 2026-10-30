"""Feeder loops survive a dependency restart (plan item 0.2)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from service_commons.exceptions import ServiceError

from task_feeder.acceptance import AcceptanceLoop
from task_feeder.config import AcceptanceConfig
from task_feeder.loop import TaskFeederLoop
from task_feeder.review import ReviewLoop

from ._runtime_fixtures import make_agent, runtime_commands

if TYPE_CHECKING:
    from pathlib import Path

BACKOFF_SECONDS = 7


def _connect_error() -> httpx.ConnectError:
    return httpx.ConnectError("task board restarting")


def _service_error() -> ServiceError:
    return ServiceError("service_unavailable", "down", 503, None)


def _acceptance_config() -> AcceptanceConfig:
    return AcceptanceConfig(
        acceptance_after_seconds=30,
        acceptance_poll_interval_seconds=5,
        min_bids_to_accept=2,
        bidding_deadline_seconds=120,
    )


def _sleep_recorder(
    monkeypatch: pytest.MonkeyPatch, module: str, loop: Any, stop_after: int
) -> list[float]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= stop_after:
            loop.stop()

    monkeypatch.setattr(f"{module}.asyncio.sleep", fake_sleep)
    return sleeps


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("error_factory", [_connect_error, _service_error])
async def test_acceptance_loop_retries_after_dependency_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_factory: Any
) -> None:
    agent = make_agent()
    agent.list_tasks = AsyncMock(side_effect=[error_factory(), []])
    with runtime_commands(tmp_path, agent) as (commands, _store):
        loop = AcceptanceLoop(agent, _acceptance_config(), lambda: datetime.now(UTC), commands)
        sleeps = _sleep_recorder(monkeypatch, "task_feeder.acceptance", loop, stop_after=2)
        await loop.run(retry_backoff_seconds=BACKOFF_SECONDS)

    assert agent.list_tasks.await_count == 2
    assert sleeps == [BACKOFF_SECONDS, 5]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("error_factory", [_connect_error, _service_error])
async def test_review_loop_retries_after_dependency_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_factory: Any
) -> None:
    agent = make_agent()
    agent.list_tasks = AsyncMock(side_effect=[error_factory(), []])
    with runtime_commands(tmp_path, agent) as (commands, _store):
        loop = ReviewLoop(agent, {}, commands)
        sleeps = _sleep_recorder(monkeypatch, "task_feeder.review", loop, stop_after=2)
        await loop.run(interval_seconds=3, retry_backoff_seconds=BACKOFF_SECONDS)

    assert agent.list_tasks.await_count == 2
    assert sleeps == [BACKOFF_SECONDS, 3]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("error_factory", [_connect_error, _service_error])
async def test_posting_loop_retries_after_dependency_error(
    monkeypatch: pytest.MonkeyPatch, error_factory: Any
) -> None:
    config = MagicMock()
    config.feed_interval_seconds = 4
    config.handle = "feeder"
    posting = MagicMock()
    posting.published_count = 0
    loop = TaskFeederLoop(make_agent(), config, posting)
    loop.feed_once = AsyncMock(side_effect=[error_factory(), None])  # type: ignore[method-assign]
    sleeps = _sleep_recorder(monkeypatch, "task_feeder.loop", loop, stop_after=2)
    await loop.run()

    assert loop.feed_once.await_count == 2
    assert sleeps == [4, 4]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_acceptance_loop_propagates_programming_errors(tmp_path: Path) -> None:
    agent = make_agent()
    agent.list_tasks = AsyncMock(side_effect=KeyError("bug"))
    with runtime_commands(tmp_path, agent) as (commands, _store):
        loop = AcceptanceLoop(agent, _acceptance_config(), lambda: datetime.now(UTC), commands)
        with pytest.raises(KeyError):
            await loop.run(retry_backoff_seconds=BACKOFF_SECONDS)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_review_loop_propagates_programming_errors(tmp_path: Path) -> None:
    agent = make_agent()
    agent.list_tasks = AsyncMock(side_effect=KeyError("bug"))
    with runtime_commands(tmp_path, agent) as (commands, _store):
        loop = ReviewLoop(agent, {}, commands)
        with pytest.raises(KeyError):
            await loop.run(interval_seconds=3, retry_backoff_seconds=BACKOFF_SECONDS)

"""Acceptance freezes full public task details, never the list summary."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import httpx
import pytest

from base_agent.runtime_intents import RuntimeIntentError
from tests.unit.task_feeder.test_mp07_native_intents import (
    _TASK_ID,
    _WORKER_ID,
    _acceptance_loop,
    _bid,
    _commands,
    _open_task,
    _PublicFeederAgent,
    _store,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


async def test_list_summary_uses_full_task_details_before_freezing(tmp_path: Path) -> None:
    agent = _PublicFeederAgent()
    agent.task_payload = _open_task()
    agent.task_payload.update(lifecycle_version=8, reward=123)
    agent.bids = [_bid("bid-selected", _WORKER_ID, 60)]
    summary = _open_task()
    del summary["escrow_id"]
    with _store(tmp_path, agent) as store:
        loop = _acceptance_loop(agent, _commands(agent, store))
        assert await loop.accept_one(summary) == "pending"
        saved = store.load(_TASK_ID, "acceptance")
        assert saved is not None
        assert saved.expected_lifecycle_version == 8
        assert saved.acknowledgment_context["frozen_task_facts"]["accepted_reward"] == 123
        assert (
            saved.acknowledgment_context["frozen_task_facts"]["escrow_id"]
            == agent.task_payload["escrow_id"]
        )
        assert agent.events.index("get_task") < agent.events.index("accept_bid")


@pytest.mark.parametrize("status", ["accepted", "expired", "cancelled"])
async def test_full_task_that_left_open_does_not_publish_stale_intent(
    tmp_path: Path, status: str
) -> None:
    agent = _PublicFeederAgent()
    agent.task_payload = {**_open_task(), "status": status, "lifecycle_version": 8}
    agent.bids = [_bid("bid-selected", _WORKER_ID, 60)]
    with _store(tmp_path, agent) as store:
        loop = _acceptance_loop(agent, _commands(agent, store))
        with pytest.raises((RuntimeIntentError, ValueError)):
            await loop.accept_one(_open_task())
        assert store.load(_TASK_ID, "acceptance") is None
        assert "accept_bid" not in agent.events


async def test_missing_detail_response_does_not_publish_or_send(tmp_path: Path) -> None:
    agent = _PublicFeederAgent()
    agent.bids = [_bid("bid-selected", _WORKER_ID, 60)]
    failure = httpx.ReadTimeout("Task detail unavailable")
    agent.get_task = AsyncMock(side_effect=failure)
    with _store(tmp_path, agent) as store:
        loop = _acceptance_loop(agent, _commands(agent, store))
        with pytest.raises(httpx.ReadTimeout, match="Task detail unavailable"):
            await loop.accept_one(_open_task())
        assert store.load(_TASK_ID, "acceptance") is None
        assert "accept_bid" not in agent.events


async def test_detail_for_another_task_does_not_publish_or_send(tmp_path: Path) -> None:
    agent = _PublicFeederAgent()
    agent.task_payload = {
        **_open_task(),
        "task_id": "t-00000000-0000-4000-8000-000000000708",
    }
    agent.bids = [_bid("bid-selected", _WORKER_ID, 60)]
    with _store(tmp_path, agent) as store:
        loop = _acceptance_loop(agent, _commands(agent, store))
        with pytest.raises((RuntimeIntentError, ValueError)):
            await loop.accept_one(_open_task())
        assert store.unfinished() == ()
        assert "accept_bid" not in agent.events

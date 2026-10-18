"""Coordinators sharing one leased journal serialize the same command slot."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from service_auth import TaskBoardCommandResponse

from base_agent.runtime_task_commands import RuntimeTaskCommands
from tests.unit.test_runtime_task_commands import (
    _agent_with_result,
    _bid_ack,
    _new_intent,
    _prepare,
    _store,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


async def test_two_coordinators_share_slot_serialization(tmp_path: Path) -> None:
    intent = _new_intent()

    async def deliver(*_args: object, **_kwargs: object) -> TaskBoardCommandResponse:
        await asyncio.sleep(0)
        return TaskBoardCommandResponse(201, _bid_ack(intent))

    agent = _agent_with_result(TaskBoardCommandResponse(201, _bid_ack(intent)))
    agent.submit_bid = AsyncMock(side_effect=deliver)
    first_prepare, second_prepare = _prepare(intent), _prepare(intent)
    with _store(tmp_path) as store:
        store.create(intent)
        first = RuntimeTaskCommands(agent, store)
        second = RuntimeTaskCommands(agent, store)
        results = await asyncio.gather(
            first.execute(task_id=intent.task_id, slot=intent.slot, prepare=first_prepare),
            second.execute(task_id=intent.task_id, slot=intent.slot, prepare=second_prepare),
        )
    assert results[0] == results[1]
    assert results[0].state == "completed"
    agent.submit_bid.assert_awaited_once()
    first_prepare.assert_not_awaited()
    second_prepare.assert_not_awaited()

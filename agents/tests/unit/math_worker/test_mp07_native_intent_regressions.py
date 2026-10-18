"""Review regressions for strict math-worker reward admission."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock

import pytest
from service_auth.financial_responses import SQLITE_MAX_INTEGER

from base_agent.runtime_intents import RuntimeIntentError
from tests.unit.math_worker import test_mp07_native_intents as worker_fixtures

if TYPE_CHECKING:
    from pathlib import Path

    from base_agent.agent import BaseAgent

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

worker_agent = worker_fixtures.worker_agent

_VALID_TASK_ID = "t-20000000-0000-4000-8000-000000000002"


@pytest.mark.parametrize(
    "reward",
    [
        True,
        "100",
        100.0,
        100.5,
        SQLITE_MAX_INTEGER + 1,
        0,
        -1,
        None,
    ],
    ids=[
        "boolean",
        "numeric-string",
        "integral-float",
        "fractional-float",
        "sqlite-overflow",
        "zero",
        "negative",
        "missing",
    ],
)
async def test_bidding_rejects_invalid_reward_before_decision_identity_or_transport(
    tmp_path: Path,
    worker_agent: BaseAgent,
    monkeypatch: pytest.MonkeyPatch,
    reward: object,
) -> None:
    root = tmp_path / "runtime"
    task = worker_fixtures._task("open", 7)
    task["reward"] = reward
    mocked = cast("Any", worker_agent)
    mocked.get_task = AsyncMock(return_value=task)
    mocked.get_balance = AsyncMock(side_effect=AssertionError("invalid reward fetched balance"))
    mocked.submit_bid = AsyncMock(side_effect=AssertionError("invalid reward sent bid"))
    llm = worker_fixtures._llm()
    llm.complete = AsyncMock(side_effect=AssertionError("invalid reward called LLM"))
    uuid4 = Mock(side_effect=AssertionError("invalid reward allocated operation ID"))
    monkeypatch.setattr("math_worker.loop.uuid.uuid4", uuid4)

    with worker_fixtures._open_commands(root, worker_agent) as (store, commands):
        loop = worker_fixtures._loop(worker_agent, llm, commands)
        with pytest.raises(RuntimeIntentError) as raised:
            await loop._phase_bidding(worker_fixtures._TASK_ID)
        assert raised.value.reason == "invalid_input"
        assert store.load(worker_fixtures._TASK_ID, "bid") is None

    mocked.get_task.assert_awaited_once_with(worker_fixtures._TASK_ID)
    mocked.get_balance.assert_not_awaited()
    mocked.submit_bid.assert_not_awaited()
    llm.complete.assert_not_awaited()
    uuid4.assert_not_called()
    worker_fixtures._assert_public_transport_only(worker_agent)


@pytest.mark.parametrize(
    "reward",
    [True, "100", 100.0, SQLITE_MAX_INTEGER + 1],
    ids=["boolean", "numeric-string", "float", "sqlite-overflow"],
)
async def test_scanning_excludes_invalid_reward_and_keeps_valid_candidate_eligible(
    tmp_path: Path,
    worker_agent: BaseAgent,
    reward: object,
) -> None:
    root = tmp_path / "runtime"
    invalid = worker_fixtures._task("open", 7)
    invalid.update(
        {
            "reward": reward,
            "title": "Malformed reward candidate",
        }
    )
    valid = worker_fixtures._task("open", 7)
    valid.update(
        {
            "task_id": _VALID_TASK_ID,
            "reward": 100,
            "title": "Valid reward candidate",
        }
    )
    mocked = cast("Any", worker_agent)
    mocked.list_tasks = AsyncMock(return_value=[invalid, valid])
    mocked.get_balance = AsyncMock(return_value={"balance": 500})
    mocked.submit_bid = AsyncMock()
    llm = worker_fixtures._llm(_VALID_TASK_ID)

    with worker_fixtures._open_commands(root, worker_agent) as (store, commands):
        selected = await worker_fixtures._loop(worker_agent, llm, commands)._phase_scanning()
        assert store.unfinished() == ()

    assert selected == _VALID_TASK_ID
    mocked.list_tasks.assert_awaited_once_with(status="open")
    mocked.get_balance.assert_awaited_once_with()
    llm.complete.assert_awaited_once()
    prompt = llm.complete.await_args.args[1]
    assert _VALID_TASK_ID in prompt
    assert worker_fixtures._TASK_ID not in prompt
    assert "Valid reward candidate" in prompt
    assert "Malformed reward candidate" not in prompt
    mocked.submit_bid.assert_not_awaited()
    worker_fixtures._assert_public_transport_only(worker_agent)

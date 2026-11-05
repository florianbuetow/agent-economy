"""Evidence for the unratified math-worker capability admission gap."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from base_agent.runtime_task_commands import RuntimeTaskCommands
from math_worker.config import MathWorkerConfig
from math_worker.loop import MathWorkerLoop
from math_worker.parser import parse_solution

pytestmark = pytest.mark.unit


def _config() -> MathWorkerConfig:
    return MathWorkerConfig(
        handle="mathbot",
        scan_interval_seconds=1,
        poll_interval_seconds=0,
        max_poll_attempts=1,
        error_backoff_seconds=1,
        min_reward=1,
        max_reward=1000,
    )


async def test_math_worker_has_no_hard_capability_gate_for_coding_task() -> None:
    """Reward eligibility plus an LLM choice can admit unsupported coding work."""
    coding = {
        "task_id": "t-coding",
        "title": "Invert a binary tree",
        "spec": "Give example Python code.",
        "reward": 100,
    }
    numeric = {
        "task_id": "t-numeric",
        "title": "Add numbers",
        "spec": "Return 2 + 2.",
        "reward": 100,
    }
    agent = MagicMock()
    agent.agent_id = "a-mathbot"
    agent.list_tasks = AsyncMock(return_value=[coding, numeric])
    agent.get_balance = AsyncMock(return_value={"balance": 0})
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(content="t-coding"))
    loop = MathWorkerLoop(
        agent=agent,
        llm=llm,
        config=_config(),
        commands=MagicMock(spec=RuntimeTaskCommands),
    )

    selected = await loop._phase_scanning()

    assert selected == "t-coding"
    assert parse_solution("I cannot provide code.\nANSWER: 7") == "7"

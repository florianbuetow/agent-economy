from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from math_worker.config import MathWorkerConfig
from math_worker.loop import MathWorkerLoop


def _make_config(**overrides: object) -> MathWorkerConfig:
    defaults = {
        "handle": "mathbot",
        "scan_interval_seconds": 1,
        "poll_interval_seconds": 0,
        "max_poll_attempts": 1,
        "error_backoff_seconds": 1,
        "min_reward": 1,
        "max_reward": 1000,
    }
    defaults.update(overrides)
    return MathWorkerConfig(**defaults)  # type: ignore[arg-type]


def _make_loop(agent: MagicMock) -> MathWorkerLoop:
    llm = MagicMock()
    return MathWorkerLoop(agent=agent, llm=llm, config=_make_config())


@pytest.mark.unit
class TestMathWorkerStatusVocabulary:
    @pytest.mark.asyncio
    async def test_scanning_lists_open_tasks(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-worker"
        agent.list_tasks = AsyncMock(return_value=[])
        loop = _make_loop(agent)

        result = await loop._phase_scanning()

        assert result is None
        agent.list_tasks.assert_awaited_once_with(status="open")

    @pytest.mark.asyncio
    async def test_waiting_for_acceptance_recognizes_accepted_task(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-worker"
        agent.get_task = AsyncMock(return_value={"status": "accepted", "worker_id": "a-worker"})
        loop = _make_loop(agent)

        accepted = await loop._phase_waiting_for_acceptance("t-1", "b-1")

        assert accepted

    @pytest.mark.asyncio
    async def test_waiting_for_acceptance_treats_cancelled_as_terminal(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-worker"
        agent.get_task = AsyncMock(return_value={"status": "cancelled", "worker_id": None})
        loop = _make_loop(agent)

        accepted = await loop._phase_waiting_for_acceptance("t-1", "b-1")

        assert not accepted

    @pytest.mark.asyncio
    async def test_waiting_for_review_recognizes_approved_task(self) -> None:
        agent = MagicMock()
        agent.get_task = AsyncMock(return_value={"status": "approved"})
        loop = _make_loop(agent)

        outcome = await loop._phase_waiting_for_review("t-1")

        assert outcome == "approved"

    @pytest.mark.asyncio
    async def test_waiting_for_review_recognizes_disputed_task(self) -> None:
        agent = MagicMock()
        agent.get_task = AsyncMock(return_value={"status": "disputed"})
        loop = _make_loop(agent)

        outcome = await loop._phase_waiting_for_review("t-1")

        assert outcome == "disputed"

    @pytest.mark.asyncio
    async def test_waiting_for_ruling_recognizes_ruled_task(self) -> None:
        agent = MagicMock()
        agent.get_task = AsyncMock(
            return_value={"status": "ruled", "worker_pct": 100, "reward": 50}
        )
        loop = _make_loop(agent)

        result = await loop._phase_waiting_for_ruling("t-1")

        assert result == {"payout": 50, "status": "ruled"}


@pytest.mark.unit
class TestMathWorkerDisputeRebuttal:
    @pytest.mark.asyncio
    async def test_disputed_phase_submits_rebuttal_for_existing_dispute(self) -> None:
        agent = MagicMock()
        agent.list_disputes = AsyncMock(return_value=[{"dispute_id": "disp-1"}])
        agent.submit_worker_rebuttal = AsyncMock(return_value={"status": "rebuttal_pending"})
        agent.file_claim = AsyncMock()

        llm = MagicMock()
        llm.complete = AsyncMock(return_value=MagicMock(content="The submitted answer is correct."))
        loop = MathWorkerLoop(agent=agent, llm=llm, config=_make_config())
        loop._phase_waiting_for_ruling = AsyncMock(return_value={"payout": 50, "status": "ruled"})

        await loop._phase_disputed(
            {
                "task_id": "t-1",
                "title": "Solve 2+2",
                "reward": 50,
                "dispute_reason": "Wrong answer",
            },
            "4",
        )

        agent.list_disputes.assert_awaited_once_with(task_id="t-1")
        agent.submit_worker_rebuttal.assert_awaited_once_with(
            "t-1",
            "disp-1",
            "The submitted answer is correct.",
        )
        agent.file_claim.assert_not_awaited()

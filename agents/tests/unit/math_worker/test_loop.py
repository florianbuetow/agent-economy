from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from base_agent.runtime_task_commands import RuntimeTaskCommands
from math_worker.config import MathWorkerConfig
from math_worker.loop import MathWorkerLoop, _ruling_payout


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
    commands = MagicMock(spec=RuntimeTaskCommands)
    return MathWorkerLoop(agent=agent, llm=llm, config=_make_config(), commands=commands)


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
    async def test_waiting_for_acceptance_returns_task_once_accepted(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-worker"
        accepted = {"status": "accepted", "worker_id": "a-worker"}
        agent.get_task = AsyncMock(return_value=accepted)
        loop = _make_loop(agent)

        assert await loop._phase_waiting_for_acceptance("t-1") == accepted

    @pytest.mark.asyncio
    async def test_waiting_for_acceptance_returns_cancelled_task_as_left_bidding(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-worker"
        cancelled = {"status": "cancelled", "worker_id": None}
        agent.get_task = AsyncMock(return_value=cancelled)
        loop = _make_loop(agent)

        assert await loop._phase_waiting_for_acceptance("t-1") == cancelled

    @pytest.mark.asyncio
    async def test_waiting_for_acceptance_still_open_returns_none_without_giving_up(
        self,
    ) -> None:
        agent = MagicMock()
        agent.agent_id = "a-worker"
        agent.get_task = AsyncMock(return_value={"status": "open", "worker_id": None})
        loop = _make_loop(agent)

        assert await loop._phase_waiting_for_acceptance("t-1") is None

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
        ruled = {"status": "ruled", "worker_pct": 100, "reward": 50}
        agent.get_task = AsyncMock(return_value=ruled)
        loop = _make_loop(agent)

        result = await loop._phase_waiting_for_ruling("t-1")

        assert result == ruled
        assert _ruling_payout(ruled) == 50

    def test_ruling_payout_is_worker_pct_of_settlement_amount_rounded_down(self) -> None:
        assert _ruling_payout({"worker_pct": 50, "accepted_reward": 61, "reward": 61}) == 30
        assert _ruling_payout({"worker_pct": 60, "accepted_price": 40, "reward": 50}) == 24
        assert _ruling_payout({"worker_pct": 0, "reward": 50}) == 0
        assert _ruling_payout({"worker_payout": 50, "reward": 50}) == 0


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
        commands = MagicMock(spec=RuntimeTaskCommands)
        loop = MathWorkerLoop(
            agent=agent,
            llm=llm,
            config=_make_config(),
            commands=commands,
        )
        loop._phase_waiting_for_ruling = AsyncMock(
            return_value={"task_id": "t-1", "status": "ruled", "worker_pct": 100, "reward": 50}
        )

        await loop._phase_disputed(
            {
                "task_id": "t-1",
                "title": "Solve 2+2",
                "reward": 50,
                "dispute_reason": "Wrong answer",
            },
            {"phase": "disputed", "bid_amount": 40, "solution": "4", "outcome": None},
        )

        agent.list_disputes.assert_awaited_once_with(task_id="t-1")
        agent.submit_worker_rebuttal.assert_awaited_once_with(
            "t-1",
            "disp-1",
            "The submitted answer is correct.",
        )
        agent.file_claim.assert_not_awaited()
        assert loop._history.records[0].payout == 50
        assert loop._history.records[0].bid_amount == 40

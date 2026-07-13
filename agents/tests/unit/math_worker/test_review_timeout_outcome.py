"""Acceptance tests for H-4 (GAP-A7): review-poll exhaustion must record
TaskOutcome.TIMEOUT with zero payout, not silently auto-approve full reward.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from math_worker.config import MathWorkerConfig
from math_worker.history import TaskOutcome
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


def _make_full_cycle_agent() -> MagicMock:
    """An agent double that drives one full cycle through to a review timeout.

    ``get_task`` is called exactly four times by ``_cycle`` when
    ``max_poll_attempts=1``: pre-bid fetch, one acceptance-poll attempt,
    pre-solve fetch, one review-poll attempt. The review-poll attempt
    returns a non-terminal status so review polling exhausts without ever
    seeing "approved" or "disputed".
    """
    agent = MagicMock()
    agent.agent_id = "a-worker"
    agent.list_tasks = AsyncMock(
        return_value=[{"task_id": "t-1", "title": "Solve X", "reward": 50}]
    )
    agent.get_balance = AsyncMock(return_value={"balance": 100})
    agent.get_task = AsyncMock(
        side_effect=[
            {"task_id": "t-1", "title": "Solve X", "reward": 50, "status": "open"},
            {"task_id": "t-1", "status": "accepted", "worker_id": "a-worker"},
            {"task_id": "t-1", "title": "Solve X", "reward": 50, "status": "accepted"},
            {"task_id": "t-1", "status": "submitted"},
        ]
    )
    agent.submit_bid = AsyncMock(return_value={"bid_id": "b-1", "amount": 40})
    agent.upload_asset = AsyncMock()
    agent.submit_deliverable = AsyncMock()
    return agent


def _make_full_cycle_llm() -> MagicMock:
    llm = MagicMock()
    llm.complete = AsyncMock(
        side_effect=[
            MagicMock(content="t-1"),
            MagicMock(content="40"),
            MagicMock(content="ANSWER: 42"),
        ]
    )
    return llm


@pytest.mark.unit
class TestReviewPollExhaustionOutcome:
    @pytest.mark.asyncio
    async def test_cycle_records_timeout_not_approved_on_review_poll_exhaustion(
        self,
    ) -> None:
        agent = _make_full_cycle_agent()
        llm = _make_full_cycle_llm()
        loop = MathWorkerLoop(agent=agent, llm=llm, config=_make_config())

        await loop._cycle()

        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.TIMEOUT
        assert record.outcome != TaskOutcome.APPROVED

    @pytest.mark.asyncio
    async def test_cycle_timeout_outcome_has_zero_payout(self) -> None:
        agent = _make_full_cycle_agent()
        llm = _make_full_cycle_llm()
        loop = MathWorkerLoop(agent=agent, llm=llm, config=_make_config())

        await loop._cycle()

        record = loop._history.records[0]
        assert record.payout == 0
        assert loop._history.total_earnings == 0


@pytest.mark.unit
class TestPhaseMachineCanonicalStatusVocabulary:
    """Guards against regressing to non-canonical (e.g. uppercase enum-name
    style) status vocabulary in the phase machine's polling comparisons.
    """

    @pytest.mark.asyncio
    async def test_scanning_queries_exact_lowercase_open_status(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-worker"
        agent.list_tasks = AsyncMock(return_value=[])
        loop = MathWorkerLoop(agent=agent, llm=MagicMock(), config=_make_config())

        await loop._phase_scanning()

        called_status = agent.list_tasks.await_args.kwargs["status"]
        assert called_status == "open"

    @pytest.mark.asyncio
    async def test_review_polling_normalizes_mixed_case_approved_and_disputed(
        self,
    ) -> None:
        agent = MagicMock()
        agent.get_task = AsyncMock(return_value={"status": "Approved"})
        loop = MathWorkerLoop(agent=agent, llm=MagicMock(), config=_make_config())
        assert await loop._phase_waiting_for_review("t-1") == "approved"

        agent.get_task = AsyncMock(return_value={"status": "DISPUTED"})
        loop = MathWorkerLoop(agent=agent, llm=MagicMock(), config=_make_config())
        assert await loop._phase_waiting_for_review("t-1") == "disputed"

    @pytest.mark.asyncio
    async def test_ruling_polling_normalizes_mixed_case_ruled(self) -> None:
        agent = MagicMock()
        agent.get_task = AsyncMock(return_value={"status": "Ruled", "worker_payout": 30})
        loop = MathWorkerLoop(agent=agent, llm=MagicMock(), config=_make_config())

        result = await loop._phase_waiting_for_ruling("t-1")

        assert result == {"payout": 30, "status": "ruled"}

"""Acceptance tests for H-4 (GAP-A7): review-poll exhaustion must record
TaskOutcome.TIMEOUT with zero payout, not silently auto-approve full reward.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from base_agent.runtime_task_commands import RuntimeTaskCommands
from math_worker.config import MathWorkerConfig
from math_worker.history import TaskOutcome
from math_worker.loop import MathWorkerLoop
from tests.unit.math_worker.runtime_fixtures import (
    TASK_ID,
    install_successful_native_transports,
    runtime_worker,
    task_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path


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


def _make_full_cycle_llm() -> MagicMock:
    llm = MagicMock()
    llm.complete = AsyncMock(
        side_effect=[
            MagicMock(content=TASK_ID),
            MagicMock(content="40"),
            MagicMock(content="ANSWER: 42"),
        ]
    )
    return llm


async def _run_timeout_cycle(tmp_path: Path) -> MathWorkerLoop:
    """Drive the real runtime command boundary to a nonterminal review snapshot."""
    async with runtime_worker(tmp_path / "runtime") as (agent, store, commands):
        mocked = cast("Any", agent)
        mocked.list_tasks = AsyncMock(return_value=[task_snapshot("open", 7)])
        mocked.get_balance = AsyncMock(return_value={"balance": 100})
        mocked.get_task = AsyncMock(
            side_effect=[
                task_snapshot("open", 7),
                task_snapshot("open", 7),
                task_snapshot("accepted", 8),
                task_snapshot("accepted", 8),
                task_snapshot("accepted", 8),
                task_snapshot("submitted", 9),
            ]
        )
        mocked.upload_asset = AsyncMock(return_value={"asset_id": "asset-solution"})
        install_successful_native_transports(agent, store)
        loop = MathWorkerLoop(agent, _make_full_cycle_llm(), _make_config(), commands)

        await loop._cycle()

    return loop


def _make_polling_loop(agent: MagicMock) -> MathWorkerLoop:
    commands = MagicMock(spec=RuntimeTaskCommands)
    return MathWorkerLoop(agent, MagicMock(), _make_config(), commands)


@pytest.mark.unit
class TestReviewPollExhaustionOutcome:
    @pytest.mark.asyncio
    async def test_cycle_records_timeout_not_approved_on_review_poll_exhaustion(
        self,
        tmp_path: Path,
    ) -> None:
        loop = await _run_timeout_cycle(tmp_path)

        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.TIMEOUT
        assert record.outcome != TaskOutcome.APPROVED

    @pytest.mark.asyncio
    async def test_cycle_timeout_outcome_has_zero_payout(self, tmp_path: Path) -> None:
        loop = await _run_timeout_cycle(tmp_path)

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
        loop = _make_polling_loop(agent)

        await loop._phase_scanning()

        called_status = agent.list_tasks.await_args.kwargs["status"]
        assert called_status == "open"

    @pytest.mark.asyncio
    async def test_review_polling_normalizes_mixed_case_approved_and_disputed(
        self,
    ) -> None:
        agent = MagicMock()
        agent.get_task = AsyncMock(return_value={"status": "Approved"})
        loop = _make_polling_loop(agent)
        assert await loop._phase_waiting_for_review("t-1") == "approved"

        agent.get_task = AsyncMock(return_value={"status": "DISPUTED"})
        loop = _make_polling_loop(agent)
        assert await loop._phase_waiting_for_review("t-1") == "disputed"

    @pytest.mark.asyncio
    async def test_ruling_polling_normalizes_mixed_case_ruled(self) -> None:
        agent = MagicMock()
        agent.get_task = AsyncMock(return_value={"status": "Ruled", "worker_payout": 30})
        loop = _make_polling_loop(agent)

        result = await loop._phase_waiting_for_ruling("t-1")

        assert result == {"payout": 30, "status": "ruled"}

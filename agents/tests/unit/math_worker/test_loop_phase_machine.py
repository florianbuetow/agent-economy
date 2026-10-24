"""Full-cycle phase-machine tests for MathWorkerLoop (T-017).

Drives ``MathWorkerLoop._cycle()`` end-to-end for each terminal outcome
(bid phase -> acceptance wait -> solve -> submit -> review poll) and pins
the ``Phase`` enum's canonical lowercase status vocabulary with a mutation
guard: reverting any member's value to legacy uppercase (e.g. ``BIDDING``
back to ``"BIDDING"``) must fail this suite.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from math_worker.config import MathWorkerConfig
from math_worker.history import TaskOutcome
from math_worker.loop import MathWorkerLoop, Phase
from tests.unit.math_worker.runtime_fixtures import (
    TASK_ID,
    WORKER_ID,
    assert_owner_binding,
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


@pytest.mark.unit
class TestPhaseEnumCanonicalVocabulary:
    """Mutation guard: every Phase member must carry its canonical
    lowercase string value. Reverting e.g. ``BIDDING = "bidding"`` to
    ``BIDDING = "BIDDING"`` must fail this test.
    """

    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (Phase.SCANNING, "scanning"),
            (Phase.BIDDING, "bidding"),
            (Phase.WAITING_FOR_ACCEPTANCE, "waiting_for_acceptance"),
            (Phase.SOLVING, "solving"),
            (Phase.SUBMITTING, "submitting"),
            (Phase.WAITING_FOR_REVIEW, "waiting_for_review"),
            (Phase.DISPUTED, "disputed"),
            (Phase.WAITING_FOR_RULING, "waiting_for_ruling"),
        ],
    )
    def test_phase_value_is_canonical_lowercase(self, member: Phase, expected: str) -> None:
        assert member.value == expected

    def test_no_phase_carries_legacy_uppercase_value(self) -> None:
        assert all(member.value == member.value.lower() for member in Phase)


@pytest.mark.unit
class TestFullCycleBidNotAccepted:
    @pytest.mark.asyncio
    async def test_bid_never_accepted_records_bid_timeout(self, tmp_path: Path) -> None:
        async with runtime_worker(tmp_path / "runtime") as (agent, store, commands):
            mocked = cast("Any", agent)
            mocked.list_tasks = AsyncMock(return_value=[task_snapshot("open", 7)])
            mocked.get_balance = AsyncMock(return_value={"balance": 100})
            mocked.get_task = AsyncMock(
                side_effect=[
                    task_snapshot("open", 7),
                    task_snapshot("open", 7),
                    task_snapshot("open", 7),
                ]
            )
            mocked.upload_asset = AsyncMock()
            install_successful_native_transports(agent, store)
            llm = MagicMock()
            llm.complete = AsyncMock(
                side_effect=[MagicMock(content=TASK_ID), MagicMock(content="40")]
            )
            loop = MathWorkerLoop(agent, llm, _make_config(), commands)
            assert_owner_binding(agent, store)

            await loop._cycle()

        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.BID_TIMEOUT
        assert record.payout == 0
        mocked.upload_asset.assert_not_awaited()


@pytest.mark.unit
class TestFullCycleSolveFailure:
    @pytest.mark.asyncio
    async def test_unparsable_solution_records_error(self, tmp_path: Path) -> None:
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
                ]
            )
            mocked.upload_asset = AsyncMock()
            install_successful_native_transports(agent, store)
            llm = MagicMock()
            llm.complete = AsyncMock(
                side_effect=[
                    MagicMock(content=TASK_ID),
                    MagicMock(content="40"),
                    MagicMock(content="   "),
                ]
            )
            loop = MathWorkerLoop(agent, llm, _make_config(), commands)

            await loop._cycle()

        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.ERROR
        assert record.payout == 0
        mocked.upload_asset.assert_not_awaited()
        mocked.submit_deliverable.assert_not_awaited()


@pytest.mark.unit
class TestFullCycleApproved:
    @pytest.mark.asyncio
    async def test_full_cycle_reaches_approved_with_full_payout(self, tmp_path: Path) -> None:
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
                    task_snapshot("approved", 10),
                ]
            )
            mocked.upload_asset = AsyncMock(return_value={"asset_id": "asset-solution"})
            install_successful_native_transports(agent, store)
            llm = MagicMock()
            llm.complete = AsyncMock(
                side_effect=[
                    MagicMock(content=TASK_ID),
                    MagicMock(content="40"),
                    MagicMock(content="ANSWER: 42"),
                ]
            )
            loop = MathWorkerLoop(agent, llm, _make_config(), commands)

            await loop._cycle()

        mocked.upload_asset.assert_awaited_once_with(TASK_ID, f"{TASK_ID}_solution.txt", b"42")
        submit_call = mocked.submit_deliverable.await_args
        assert submit_call.args == (TASK_ID,)
        assert submit_call.kwargs["expected_lifecycle_version"] == 8
        assert str(submit_call.kwargs["operation_id"]).startswith("op-")
        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.APPROVED
        assert record.payout == 50
        assert loop._history.total_earnings == 50


@pytest.mark.unit
class TestFullCycleDisputed:
    @pytest.mark.asyncio
    async def test_full_cycle_disputed_won_records_partial_payout(self, tmp_path: Path) -> None:
        loop, mocked = await self._run_disputed_cycle(tmp_path, worker_payout=30)

        mocked.submit_worker_rebuttal.assert_awaited_once_with(
            TASK_ID, "disp-1", "The submitted answer is correct."
        )
        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.DISPUTED_WON
        assert record.payout == 30

    @pytest.mark.asyncio
    async def test_full_cycle_disputed_lost_records_zero_payout(self, tmp_path: Path) -> None:
        loop, _mocked = await self._run_disputed_cycle(tmp_path, worker_payout=0)

        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.DISPUTED_LOST
        assert record.payout == 0

    async def _run_disputed_cycle(
        self,
        tmp_path: Path,
        *,
        worker_payout: int,
    ) -> tuple[MathWorkerLoop, Any]:
        async with runtime_worker(tmp_path / "runtime") as (agent, store, commands):
            mocked = cast("Any", agent)
            disputed = task_snapshot("disputed", 10)
            disputed.update({"dispute_id": "disp-1", "dispute_reason": "Wrong answer"})
            ruled = task_snapshot("ruled", 11)
            ruled["worker_payout"] = worker_payout
            mocked.list_tasks = AsyncMock(return_value=[task_snapshot("open", 7)])
            mocked.get_balance = AsyncMock(return_value={"balance": 100})
            mocked.get_task = AsyncMock(
                side_effect=[
                    task_snapshot("open", 7),
                    task_snapshot("open", 7),
                    task_snapshot("accepted", 8),
                    task_snapshot("accepted", 8),
                    task_snapshot("accepted", 8),
                    task_snapshot("disputed", 10),
                    disputed,
                    ruled,
                ]
            )
            mocked.upload_asset = AsyncMock(return_value={"asset_id": "asset-solution"})
            mocked.submit_worker_rebuttal = AsyncMock(return_value={"status": "rebuttal_pending"})
            install_successful_native_transports(agent, store)
            llm = MagicMock()
            llm.complete = AsyncMock(
                side_effect=[
                    MagicMock(content=TASK_ID),
                    MagicMock(content="40"),
                    MagicMock(content="ANSWER: 42"),
                    MagicMock(content="The submitted answer is correct."),
                ]
            )
            loop = MathWorkerLoop(agent, llm, _make_config(), commands)

            await loop._cycle()

        assert agent.agent_id == WORKER_ID
        return loop, mocked

"""Full-cycle phase-machine tests for MathWorkerLoop (T-017).

Drives ``MathWorkerLoop._cycle()`` end-to-end for each terminal outcome
(bid phase -> acceptance wait -> solve -> submit -> review poll) and pins
the ``Phase`` enum's canonical lowercase status vocabulary with a mutation
guard: reverting any member's value to legacy uppercase (e.g. ``BIDDING``
back to ``"BIDDING"``) must fail this suite.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from math_worker.config import MathWorkerConfig
from math_worker.history import TaskOutcome
from math_worker.loop import MathWorkerLoop, Phase


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
    async def test_bid_never_accepted_records_bid_timeout(self) -> None:
        agent = MagicMock()
        agent.agent_id = "a-worker"
        agent.list_tasks = AsyncMock(
            return_value=[{"task_id": "t-1", "title": "Solve X", "reward": 50}]
        )
        agent.get_balance = AsyncMock(return_value={"balance": 100})
        agent.get_task = AsyncMock(
            side_effect=[
                {"task_id": "t-1", "title": "Solve X", "reward": 50, "status": "open"},
                {"task_id": "t-1", "status": "open", "worker_id": None},
            ]
        )
        agent.submit_bid = AsyncMock(return_value={"bid_id": "b-1", "amount": 40})

        llm = MagicMock()
        llm.complete = AsyncMock(
            side_effect=[
                MagicMock(content="t-1"),
                MagicMock(content="40"),
            ]
        )

        loop = MathWorkerLoop(agent=agent, llm=llm, config=_make_config())
        await loop._cycle()

        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.BID_TIMEOUT
        assert record.payout == 0
        agent.upload_asset.assert_not_called()


@pytest.mark.unit
class TestFullCycleSolveFailure:
    @pytest.mark.asyncio
    async def test_unparsable_solution_records_error(self) -> None:
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
            ]
        )
        agent.submit_bid = AsyncMock(return_value={"bid_id": "b-1", "amount": 40})

        llm = MagicMock()
        llm.complete = AsyncMock(
            side_effect=[
                MagicMock(content="t-1"),
                MagicMock(content="40"),
                MagicMock(content="   "),  # unparsable -> parse_solution returns None
            ]
        )

        loop = MathWorkerLoop(agent=agent, llm=llm, config=_make_config())
        await loop._cycle()

        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.ERROR
        assert record.payout == 0
        agent.upload_asset.assert_not_called()
        agent.submit_deliverable.assert_not_called()


@pytest.mark.unit
class TestFullCycleApproved:
    @pytest.mark.asyncio
    async def test_full_cycle_reaches_approved_with_full_payout(self) -> None:
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
                {"task_id": "t-1", "status": "approved"},
            ]
        )
        agent.submit_bid = AsyncMock(return_value={"bid_id": "b-1", "amount": 40})
        agent.upload_asset = AsyncMock()
        agent.submit_deliverable = AsyncMock()

        llm = MagicMock()
        llm.complete = AsyncMock(
            side_effect=[
                MagicMock(content="t-1"),
                MagicMock(content="40"),
                MagicMock(content="ANSWER: 42"),
            ]
        )

        loop = MathWorkerLoop(agent=agent, llm=llm, config=_make_config())
        await loop._cycle()

        agent.upload_asset.assert_awaited_once()
        agent.submit_deliverable.assert_awaited_once_with("t-1")
        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.APPROVED
        assert record.payout == 50
        assert loop._history.total_earnings == 50


@pytest.mark.unit
class TestFullCycleDisputed:
    @pytest.mark.asyncio
    async def test_full_cycle_disputed_won_records_partial_payout(self) -> None:
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
                {"task_id": "t-1", "status": "disputed"},
                {
                    "task_id": "t-1",
                    "title": "Solve X",
                    "reward": 50,
                    "status": "disputed",
                    "dispute_id": "disp-1",
                    "dispute_reason": "Wrong answer",
                },
                {"task_id": "t-1", "status": "ruled", "worker_payout": 30},
            ]
        )
        agent.submit_bid = AsyncMock(return_value={"bid_id": "b-1", "amount": 40})
        agent.upload_asset = AsyncMock()
        agent.submit_deliverable = AsyncMock()
        agent.submit_worker_rebuttal = AsyncMock(return_value={"status": "rebuttal_pending"})

        llm = MagicMock()
        llm.complete = AsyncMock(
            side_effect=[
                MagicMock(content="t-1"),
                MagicMock(content="40"),
                MagicMock(content="ANSWER: 42"),
                MagicMock(content="The submitted answer is correct."),
            ]
        )

        loop = MathWorkerLoop(agent=agent, llm=llm, config=_make_config())
        await loop._cycle()

        agent.submit_worker_rebuttal.assert_awaited_once_with(
            "t-1", "disp-1", "The submitted answer is correct."
        )
        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.DISPUTED_WON
        assert record.payout == 30

    @pytest.mark.asyncio
    async def test_full_cycle_disputed_lost_records_zero_payout(self) -> None:
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
                {"task_id": "t-1", "status": "disputed"},
                {
                    "task_id": "t-1",
                    "title": "Solve X",
                    "reward": 50,
                    "status": "disputed",
                    "dispute_id": "disp-1",
                    "dispute_reason": "Wrong answer",
                },
                {"task_id": "t-1", "status": "ruled", "worker_payout": 0},
            ]
        )
        agent.submit_bid = AsyncMock(return_value={"bid_id": "b-1", "amount": 40})
        agent.upload_asset = AsyncMock()
        agent.submit_deliverable = AsyncMock()
        agent.submit_worker_rebuttal = AsyncMock(return_value={"status": "rebuttal_pending"})

        llm = MagicMock()
        llm.complete = AsyncMock(
            side_effect=[
                MagicMock(content="t-1"),
                MagicMock(content="40"),
                MagicMock(content="ANSWER: 42"),
                MagicMock(content="The submitted answer is correct."),
            ]
        )

        loop = MathWorkerLoop(agent=agent, llm=llm, config=_make_config())
        await loop._cycle()

        assert len(loop._history.records) == 1
        record = loop._history.records[0]
        assert record.outcome == TaskOutcome.DISPUTED_LOST
        assert record.payout == 0

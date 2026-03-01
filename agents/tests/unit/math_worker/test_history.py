"""Unit tests for math_worker.history."""

import pytest

from math_worker.history import AgentHistory, TaskOutcome


@pytest.mark.unit
class TestAgentHistory:
    def test_starts_empty(self) -> None:
        history = AgentHistory()
        assert history.tasks_completed == 0
        assert history.total_earnings == 0
        assert history.tasks_approved == 0
        assert history.tasks_disputed == 0

    def test_record_approved_task(self) -> None:
        history = AgentHistory()
        record = history.record(
            task_id="t-1",
            title="Add numbers",
            reward=100,
            bid_amount=100,
            outcome=TaskOutcome.APPROVED,
            solution="42",
            payout=100,
        )
        assert record.task_id == "t-1"
        assert record.outcome == TaskOutcome.APPROVED
        assert history.tasks_completed == 1
        assert history.total_earnings == 100
        assert history.tasks_approved == 1

    def test_record_disputed_tasks(self) -> None:
        history = AgentHistory()
        history.record(
            task_id="t-1",
            title="A",
            reward=100,
            bid_amount=100,
            outcome=TaskOutcome.DISPUTED_WON,
            solution="x",
            payout=80,
        )
        history.record(
            task_id="t-2",
            title="B",
            reward=200,
            bid_amount=200,
            outcome=TaskOutcome.DISPUTED_LOST,
            solution="y",
            payout=0,
        )
        assert history.tasks_disputed == 2
        assert history.total_earnings == 80

    def test_multiple_outcomes(self) -> None:
        history = AgentHistory()
        history.record(
            task_id="t-1",
            title="A",
            reward=100,
            bid_amount=100,
            outcome=TaskOutcome.APPROVED,
            solution="1",
            payout=100,
        )
        history.record(
            task_id="t-2",
            title="B",
            reward=50,
            bid_amount=50,
            outcome=TaskOutcome.BID_TIMEOUT,
            solution=None,
            payout=0,
        )
        assert history.tasks_completed == 2
        assert history.tasks_approved == 1
        assert history.total_earnings == 100

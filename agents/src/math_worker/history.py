"""In-memory history of past task outcomes for LLM context."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class TaskOutcome(Enum):
    """How a task ended for this agent."""

    APPROVED = "approved"
    DISPUTED_WON = "disputed_won"
    DISPUTED_LOST = "disputed_lost"
    BID_REJECTED = "bid_rejected"
    BID_TIMEOUT = "bid_timeout"
    ERROR = "error"


@dataclass
class TaskRecord:
    """One completed task cycle."""

    task_id: str
    title: str
    reward: int
    bid_amount: int
    outcome: TaskOutcome
    solution: str | None
    payout: int
    started_at: datetime
    finished_at: datetime


@dataclass
class AgentHistory:
    """Tracks the agent's task history within a single run."""

    records: list[TaskRecord] = field(default_factory=list)

    @property
    def total_earnings(self) -> int:
        """Sum of all payouts received."""
        return sum(r.payout for r in self.records)

    @property
    def tasks_completed(self) -> int:
        """Number of tasks that reached a terminal state."""
        return len(self.records)

    @property
    def tasks_approved(self) -> int:
        """Number of tasks approved without dispute."""
        return sum(1 for r in self.records if r.outcome == TaskOutcome.APPROVED)

    @property
    def tasks_disputed(self) -> int:
        """Number of tasks that went to dispute."""
        return sum(
            1
            for r in self.records
            if r.outcome in (TaskOutcome.DISPUTED_WON, TaskOutcome.DISPUTED_LOST)
        )

    def record(
        self,
        task_id: str,
        title: str,
        reward: int,
        bid_amount: int,
        outcome: TaskOutcome,
        solution: str | None,
        payout: int,
    ) -> TaskRecord:
        """Record a completed task cycle.

        Args:
            task_id:    Platform task identifier.
            title:      Human-readable task title.
            reward:     Original reward posted.
            bid_amount: What the agent bid.
            outcome:    Terminal outcome.
            solution:   The solution text submitted (if any).
            payout:     Credits actually received.

        Returns:
            The created TaskRecord.
        """
        now = datetime.now(tz=timezone.utc)
        entry = TaskRecord(
            task_id=task_id,
            title=title,
            reward=reward,
            bid_amount=bid_amount,
            outcome=outcome,
            solution=solution,
            payout=payout,
            started_at=now,
            finished_at=now,
        )
        self.records.append(entry)
        logger.info(
            "Task %s finished: outcome=%s payout=%d total_earnings=%d",
            task_id,
            outcome.value,
            payout,
            self.total_earnings,
        )
        return entry

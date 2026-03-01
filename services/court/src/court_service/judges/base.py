"""Judge interfaces and value types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class JudgeVote:
    """A single judge vote for a dispute."""

    judge_id: str
    worker_pct: int
    reasoning: str
    voted_at: str


@dataclass
class DisputeContext:
    """Inputs provided to judges during evaluation."""

    task_spec: str
    deliverables: list[str]
    claim: str
    rebuttal: str | None
    task_title: str
    reward: int


class Judge(ABC):
    """Abstract judge contract."""

    @abstractmethod
    async def evaluate(self, context: DisputeContext) -> JudgeVote:
        """Evaluate a dispute and return a vote."""


class MockJudge(Judge):
    """Deterministic judge implementation for local/testing use."""

    def __init__(self, judge_id: str, fixed_worker_pct: int, reasoning: str) -> None:
        self._judge_id = judge_id
        self._fixed_worker_pct = fixed_worker_pct
        self._reasoning = reasoning

    async def evaluate(self, _context: DisputeContext) -> JudgeVote:
        """Return a fixed vote without external calls."""
        return JudgeVote(
            judge_id=self._judge_id,
            worker_pct=self._fixed_worker_pct,
            reasoning=self._reasoning,
            voted_at=_utc_now_iso(),
        )

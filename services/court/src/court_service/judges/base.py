"""Judge interfaces and value types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from court_service.judges.output import JudgeFindings, validate_rating

if TYPE_CHECKING:
    from court_service.services.evidence_bundle import EvidenceBundle


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class JudgeVote:
    """A single judge vote: payout plus independent spec and delivery findings."""

    judge_id: str
    worker_pct: int
    spec_quality: str
    spec_quality_explanation: str
    delivery_quality: str
    delivery_quality_explanation: str
    reasoning: str
    voted_at: str

    @classmethod
    def from_findings(cls, judge_id: str, findings: JudgeFindings, voted_at: str) -> JudgeVote:
        """Build a vote from validated findings."""
        return cls(
            judge_id=judge_id,
            worker_pct=findings.worker_pct,
            spec_quality=findings.spec_quality,
            spec_quality_explanation=findings.spec_quality_explanation,
            delivery_quality=findings.delivery_quality,
            delivery_quality_explanation=findings.delivery_quality_explanation,
            reasoning=findings.reasoning,
            voted_at=voted_at,
        )

    def as_record(self) -> dict[str, object]:
        """The persisted per-vote record."""
        return {
            "judge_id": self.judge_id,
            "worker_pct": self.worker_pct,
            "spec_quality": self.spec_quality,
            "spec_quality_explanation": self.spec_quality_explanation,
            "delivery_quality": self.delivery_quality,
            "delivery_quality_explanation": self.delivery_quality_explanation,
            "reasoning": self.reasoning,
            "voted_at": self.voted_at,
        }


@dataclass(frozen=True)
class DisputeContext:
    """Inputs provided to judges: task header plus the immutable evidence bundle."""

    task_title: str
    reward: int
    evidence: EvidenceBundle


class Judge(ABC):
    """Abstract judge contract."""

    @abstractmethod
    async def evaluate(self, context: DisputeContext) -> JudgeVote:
        """Evaluate a dispute and return a vote."""


class MockJudge(Judge):
    """Deterministic judge implementation for local/testing use."""

    def __init__(
        self,
        judge_id: str,
        fixed_worker_pct: int,
        spec_quality: str,
        delivery_quality: str,
        reasoning: str,
    ) -> None:
        self._judge_id = judge_id
        self._findings = JudgeFindings(
            worker_pct=fixed_worker_pct,
            spec_quality=validate_rating("spec_quality", spec_quality),
            spec_quality_explanation=reasoning,
            delivery_quality=validate_rating("delivery_quality", delivery_quality),
            delivery_quality_explanation=reasoning,
            reasoning=reasoning,
        )

    async def evaluate(self, context: DisputeContext) -> JudgeVote:
        """Return a fixed vote without external calls."""
        _ = context
        return JudgeVote.from_findings(self._judge_id, self._findings, _utc_now_iso())

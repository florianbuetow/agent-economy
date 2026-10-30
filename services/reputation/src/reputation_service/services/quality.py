"""Service-owned quality score (business rules 6 and 7, Q-M3 decisions).

Pure Python — no FastAPI imports.

Formula ``quality-v1``: each dimension is scored separately. Every eligible task
contributes exactly one observation with equal weight; the Court finding for a
task replaces the peer finding for that task. The score is the mean of the
per-task ratings on a 0-100 scale, rounded once half-up to an integer. An agent
with no eligible observation is explicitly unrated (``score`` null).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from reputation_service.services.feedback import (
    ELIGIBLE_TASK_STATUSES,
    SOURCE_COURT,
    SOURCE_PEER,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from reputation_service.services.task_board_client import TaskParties
    from reputation_service.types import FeedbackRecord

FORMULA_VERSION = "quality-v1"
RATING_POINTS: dict[str, int] = {
    "dissatisfied": 0,
    "satisfied": 50,
    "extremely_satisfied": 100,
}
# Feedback category -> public dimension name, and which task party it rates.
DIMENSIONS: dict[str, tuple[str, str]] = {
    "spec_quality": ("specification_quality", "poster"),
    "delivery_quality": ("delivery_quality", "worker"),
}

REASON_COURT_OVERRIDE = "court_override"
REASON_TASK_NOT_ELIGIBLE = "task_not_eligible"
REASON_WRONG_SUBJECT = "wrong_subject"
REASON_NOT_TASK_PARTY = "not_task_party"
REASON_UNKNOWN_RATING = "unknown_rating"
REASON_DUPLICATE = "duplicate_observation"


class AgentDirectory(Protocol):
    """Identity agent lookup used to recognise the configured operator."""

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class _Observation:
    record: FeedbackRecord
    included: bool
    exclusion_reason: str | None


def _exclusion_reason(
    record: FeedbackRecord,
    task: TaskParties | None,
    subject_role: str,
) -> str | None:
    """Return why a visible record cannot be a quality observation, or None."""
    if task is None or task.status not in ELIGIBLE_TASK_STATUSES:
        return REASON_TASK_NOT_ELIGIBLE
    subject_id = task.poster_id if subject_role == "poster" else task.worker_id
    if record.to_agent_id != subject_id:
        return REASON_WRONG_SUBJECT
    if record.rating not in RATING_POINTS:
        return REASON_UNKNOWN_RATING
    if record.source == SOURCE_PEER:
        counterparty = task.worker_id if subject_role == "poster" else task.poster_id
        if record.from_agent_id != counterparty:
            return REASON_NOT_TASK_PARTY
    elif record.source != SOURCE_COURT:
        return REASON_NOT_TASK_PARTY
    return None


def _observations(
    records: list[FeedbackRecord],
    tasks: Mapping[str, TaskParties | None],
    subject_role: str,
) -> list[_Observation]:
    """Classify one dimension's visible records; at most one included per task."""
    candidates: dict[str, list[FeedbackRecord]] = {}
    result: list[_Observation] = []
    for record in records:
        reason = _exclusion_reason(record, tasks.get(record.task_id), subject_role)
        if reason is None:
            candidates.setdefault(record.task_id, []).append(record)
        else:
            result.append(_Observation(record, included=False, exclusion_reason=reason))
    for task_records in candidates.values():
        court = [r for r in task_records if r.source == SOURCE_COURT]
        preferred = sorted(court or task_records, key=lambda r: r.submitted_at)
        # The latest Court finding is final; with no Court finding the single peer
        # observation stands (the store keeps one per sender, target and task).
        chosen = preferred[-1] if court else preferred[0]
        for record in task_records:
            if record is chosen:
                result.append(_Observation(record, included=True, exclusion_reason=None))
            elif court and record.source == SOURCE_PEER:
                result.append(_Observation(record, False, REASON_COURT_OVERRIDE))
            else:
                result.append(_Observation(record, False, REASON_DUPLICATE))
    return result


def _half_up_mean(points: list[int]) -> int:
    return (2 * sum(points) + len(points)) // (2 * len(points))


def _dimension(
    observations: list[_Observation],
    can_see_task: Callable[[str], bool] | None,
) -> dict[str, Any]:
    included = [o.record for o in observations if o.included]
    points = [RATING_POINTS[r.rating] for r in included]
    dimension: dict[str, Any] = {
        "rated": bool(points),
        "score": _half_up_mean(points) if points else None,
        "task_count": len(included),
        "rating_counts": {
            rating: sum(1 for r in included if r.rating == rating)
            for rating in ("extremely_satisfied", "satisfied", "dissatisfied")
        },
    }
    if can_see_task is not None:
        dimension["provenance"] = [
            {
                "feedback_id": o.record.feedback_id,
                "task_id": o.record.task_id,
                "source": o.record.source,
                "from_agent_id": o.record.from_agent_id,
                "rating": o.record.rating,
                "submitted_at": o.record.submitted_at,
                "included": o.included,
                "exclusion_reason": o.exclusion_reason,
            }
            for o in sorted(observations, key=lambda o: o.record.submitted_at)
            if can_see_task(o.record.task_id)
        ]
    return dimension


def provenance_filter(
    viewer_id: str | None,
    *,
    subject_id: str,
    viewer_is_operator: bool,
    tasks: Mapping[str, TaskParties | None],
) -> Callable[[str], bool] | None:
    """Return a per-task visibility check for source records, or None for none.

    The subject and the operator see every source record. Any other signed
    viewer sees only records of tasks in which they are poster or worker; a
    viewer who participates in none of the subject's tasks gets no provenance.
    """
    if viewer_id is None:
        return None
    if viewer_id == subject_id or viewer_is_operator:
        return lambda _task_id: True
    own_tasks = {
        task_id
        for task_id, task in tasks.items()
        if task is not None and viewer_id in (task.poster_id, task.worker_id)
    }
    if not own_tasks:
        return None
    return lambda task_id: task_id in own_tasks


def compute_quality(
    subject_id: str,
    records: list[FeedbackRecord],
    tasks: Mapping[str, TaskParties | None],
    can_see_task: Callable[[str], bool] | None,
) -> dict[str, Any]:
    """Build the public quality report for ``subject_id``.

    ``records`` are the feedback rows about the subject; sealed rows are dropped
    here and are never scored or disclosed. ``tasks`` maps each referenced task
    id to its Task Board facts, or None when Task Board does not know the task.
    """
    report: dict[str, Any] = {"agent_id": subject_id, "formula_version": FORMULA_VERSION}
    visible = [r for r in records if r.visible and r.to_agent_id == subject_id]
    for category, (name, subject_role) in DIMENSIONS.items():
        rows = [r for r in visible if r.category == category]
        report[name] = _dimension(_observations(rows, tasks, subject_role), can_see_task)
    return report


__all__ = [
    "FORMULA_VERSION",
    "AgentDirectory",
    "compute_quality",
    "provenance_filter",
]

"""
Feedback business logic.

Pure Python — no FastAPI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from reputation_service.services.exceptions import DuplicateFeedbackError

if TYPE_CHECKING:
    from collections.abc import Callable

    from reputation_service.services.protocol import FeedbackStorageInterface
    from reputation_service.services.task_board_client import TaskParties
    from reputation_service.types import FeedbackRecord


def _system_clock() -> datetime:
    """Return the real current UTC datetime."""
    return datetime.now(UTC)


# Injectable clock seam (mirrors service_auth.signing._clock). Tests override the
# module attribute (e.g. ``feedback._clock = lambda: frozen``) to control the "now"
# reference the reveal-timeout cutoff in reveal_timed_out_feedback() is computed from.
_clock: Callable[[], datetime] = _system_clock

VALID_CATEGORIES: frozenset[str] = frozenset({"spec_quality", "delivery_quality"})
VALID_RATINGS: frozenset[str] = frozenset({"dissatisfied", "satisfied", "extremely_satisfied"})
REQUIRED_FIELDS: list[str] = ["task_id", "from_agent_id", "to_agent_id", "category", "rating"]


@dataclass
class ValidationError:
    """Validation failure result."""

    error: str
    message: str
    status_code: int
    details: dict[str, object]


def _validate_required_fields(body: dict[str, object]) -> ValidationError | None:
    """Validate that required fields are present, non-null, non-empty strings."""
    # 1. invalid_field_type — check that required fields are strings (not int, bool, list, dict)
    for field_name in REQUIRED_FIELDS:
        if field_name in body:
            value = body[field_name]
            if value is not None and not isinstance(value, str):
                return ValidationError(
                    error="invalid_field_type",
                    message=f"Field '{field_name}' must be a string",
                    status_code=400,
                    details={"field": field_name},
                )

    # 2. missing_field — check required fields are present, non-null, non-empty
    for field_name in REQUIRED_FIELDS:
        value = body.get(field_name)
        if value is None or (isinstance(value, str) and value == ""):
            return ValidationError(
                error="missing_field",
                message=f"Field '{field_name}' is required and must be a non-empty string",
                status_code=400,
                details={"field": field_name},
            )

    return None


def validate_feedback(body: dict[str, object], max_comment_length: int) -> ValidationError | None:
    """
    Validate feedback submission body.

    Validation order:
    invalid_field_type -> missing_field -> self_feedback ->
    invalid_category -> invalid_rating -> comment_too_long -> feedback_exists

    feedback_exists is checked separately during insert.

    Returns ValidationError if invalid, None if valid.
    """
    field_error = _validate_required_fields(body)
    if field_error is not None:
        return field_error

    # At this point all required fields are guaranteed to be non-empty strings
    from_agent_id = str(body["from_agent_id"])
    to_agent_id = str(body["to_agent_id"])
    category = str(body["category"])
    rating = str(body["rating"])

    # 3. self_feedback
    if from_agent_id == to_agent_id:
        return ValidationError(
            error="self_feedback",
            message="An agent cannot rate itself",
            status_code=400,
            details={"from_agent_id": from_agent_id, "to_agent_id": to_agent_id},
        )

    # 4. invalid_category
    if category not in VALID_CATEGORIES:
        return ValidationError(
            error="invalid_category",
            message=f"Category must be one of: {', '.join(sorted(VALID_CATEGORIES))}",
            status_code=400,
            details={"category": category, "valid_categories": sorted(VALID_CATEGORIES)},
        )

    # 5. invalid_rating
    if rating not in VALID_RATINGS:
        return ValidationError(
            error="invalid_rating",
            message=f"Rating must be one of: {', '.join(sorted(VALID_RATINGS))}",
            status_code=400,
            details={"rating": rating, "valid_ratings": sorted(VALID_RATINGS)},
        )

    # 6. comment_too_long
    comment = body.get("comment")
    if comment is not None and isinstance(comment, str) and len(comment) > max_comment_length:
        return ValidationError(
            error="comment_too_long",
            message=f"Comment exceeds maximum length of {max_comment_length} codepoints",
            status_code=400,
            details={"max_length": max_comment_length, "actual_length": len(comment)},
        )

    return None


ELIGIBLE_TASK_STATUSES: frozenset[str] = frozenset({"approved", "ruled"})
# The category each reviewer role may submit: the poster judges delivery, the
# worker judges the specification.
CATEGORY_FOR_REVIEWER: dict[str, str] = {
    "poster": "delivery_quality",
    "worker": "spec_quality",
}
SOURCE_PEER = "peer"
SOURCE_COURT = "court"


@dataclass
class FeedbackReplay:
    """An identical resubmission of feedback that is already stored."""

    record: FeedbackRecord


def authorize_peer_feedback(
    task: TaskParties,
    body: dict[str, object],
) -> ValidationError | None:
    """Check that the task authorizes this peer feedback.

    Requires an eligible task outcome, that sender and target are exactly the
    task's poster and worker, and that the category matches the direction.
    ``body`` must already have passed ``validate_feedback``.
    """
    from_agent_id = str(body["from_agent_id"])
    to_agent_id = str(body["to_agent_id"])
    category = str(body["category"])

    if task.status not in ELIGIBLE_TASK_STATUSES:
        return ValidationError(
            error="task_not_eligible",
            message="Feedback is accepted only for approved or ruled tasks",
            status_code=409,
            details={"task_id": task.task_id, "status": task.status},
        )

    parties = {task.poster_id: "poster"}
    if task.worker_id is not None:
        parties[task.worker_id] = "worker"
    reviewer_role = parties.get(from_agent_id)
    if reviewer_role is None or to_agent_id not in parties or to_agent_id == from_agent_id:
        return ValidationError(
            error="not_task_party",
            message="Feedback must be between the task's poster and worker",
            status_code=403,
            details={"task_id": task.task_id},
        )

    expected_category = CATEGORY_FOR_REVIEWER[reviewer_role]
    if category != expected_category:
        return ValidationError(
            error="category_direction_mismatch",
            message=f"A {reviewer_role} may only submit {expected_category} feedback",
            status_code=400,
            details={"category": category, "expected_category": expected_category},
        )
    return None


def _find_existing(
    store: FeedbackStorageInterface,
    task_id: str,
    from_agent_id: str,
    to_agent_id: str,
) -> FeedbackRecord | None:
    for record in store.get_by_task(task_id):
        if record.from_agent_id == from_agent_id and record.to_agent_id == to_agent_id:
            return record
    return None


def submit_feedback(
    store: FeedbackStorageInterface,
    body: dict[str, object],
    max_comment_length: int,
    *,
    force_visible: bool,
    source: str,
) -> FeedbackRecord | FeedbackReplay | ValidationError:
    """
    Validate and store a feedback submission.

    Args:
        force_visible: If True, the record is immediately visible (bypasses
            sealed feedback rules). Used for platform-submitted ruling feedback.
        source: ``"peer"`` or ``"court"`` provenance stored with the record.

    Returns FeedbackRecord when stored, FeedbackReplay when an identical record
    already exists, ValidationError on failure (409 when the stored record differs).
    """
    # Validate input
    validation_error = validate_feedback(body, max_comment_length)
    if validation_error is not None:
        return validation_error

    task_id = str(body["task_id"])
    from_agent_id = str(body["from_agent_id"])
    to_agent_id = str(body["to_agent_id"])
    category = str(body["category"])
    rating = str(body["rating"])

    # Extract comment (optional)
    raw_comment = body.get("comment")
    comment: str | None
    if raw_comment is None:
        comment = None
    elif isinstance(raw_comment, str):
        comment = raw_comment
    else:
        comment = None

    # Insert via store — handles uniqueness + mutual reveal atomically
    try:
        record = store.insert_feedback(
            task_id=task_id,
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            category=category,
            rating=rating,
            comment=comment,
            force_visible=force_visible,
            source=source,
        )
    except DuplicateFeedbackError:
        existing = _find_existing(store, task_id, from_agent_id, to_agent_id)
        if existing is not None and (
            existing.category,
            existing.rating,
            existing.comment,
            existing.source,
        ) == (category, rating, comment, source):
            return FeedbackReplay(record=existing)
        return ValidationError(
            error="feedback_exists",
            message="Feedback already submitted for this task, from_agent, to_agent combination",
            status_code=409,
            details={
                "task_id": task_id,
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
            },
        )

    return record


def reveal_timed_out_feedback(
    store: FeedbackStorageInterface,
    reveal_timeout_seconds: int,
) -> int:
    """Durably reveal sealed feedback older than the reveal timeout.

    The store flips the stored ``visible`` flag and emits one ``feedback.revealed``
    event per newly revealed row, so every reader (API and UI) sees the same state.
    Returns the number of rows revealed.
    """
    cutoff = _clock() - timedelta(seconds=reveal_timeout_seconds)
    return store.reveal_expired(cutoff.isoformat())


def get_feedback_by_id(
    store: FeedbackStorageInterface,
    feedback_id: str,
) -> FeedbackRecord | None:
    """Get a feedback record by ID, returning None if not found or still sealed."""
    record = store.get_by_id(feedback_id)
    if record is None or not record.visible:
        return None
    return record


def get_feedback_for_task(
    store: FeedbackStorageInterface,
    task_id: str,
) -> list[FeedbackRecord]:
    """Get all visible feedback for a task, sorted by submitted_at."""
    return [r for r in store.get_by_task(task_id) if r.visible]


def get_feedback_for_agent(
    store: FeedbackStorageInterface,
    agent_id: str,
) -> list[FeedbackRecord]:
    """Get all visible feedback about an agent (to_agent_id), sorted by submitted_at."""
    return [r for r in store.get_by_agent(agent_id) if r.visible]

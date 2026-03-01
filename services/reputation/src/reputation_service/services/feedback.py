"""
Feedback business logic.

Pure Python — no FastAPI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from reputation_service.services.feedback_store import DuplicateFeedbackError

if TYPE_CHECKING:
    from reputation_service.core.state import FeedbackRecord
    from reputation_service.services.feedback_store import FeedbackStore

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
    # 1. INVALID_FIELD_TYPE — check that required fields are strings (not int, bool, list, dict)
    for field_name in REQUIRED_FIELDS:
        if field_name in body:
            value = body[field_name]
            if value is not None and not isinstance(value, str):
                return ValidationError(
                    error="INVALID_FIELD_TYPE",
                    message=f"Field '{field_name}' must be a string",
                    status_code=400,
                    details={"field": field_name},
                )

    # 2. MISSING_FIELD — check required fields are present, non-null, non-empty
    for field_name in REQUIRED_FIELDS:
        value = body.get(field_name)
        if value is None or (isinstance(value, str) and value == ""):
            return ValidationError(
                error="MISSING_FIELD",
                message=f"Field '{field_name}' is required and must be a non-empty string",
                status_code=400,
                details={"field": field_name},
            )

    return None


def validate_feedback(body: dict[str, object], max_comment_length: int) -> ValidationError | None:
    """
    Validate feedback submission body.

    Validation order:
    INVALID_FIELD_TYPE -> MISSING_FIELD -> SELF_FEEDBACK ->
    INVALID_CATEGORY -> INVALID_RATING -> COMMENT_TOO_LONG -> FEEDBACK_EXISTS

    FEEDBACK_EXISTS is checked separately during insert.

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

    # 3. SELF_FEEDBACK
    if from_agent_id == to_agent_id:
        return ValidationError(
            error="SELF_FEEDBACK",
            message="An agent cannot rate itself",
            status_code=400,
            details={"from_agent_id": from_agent_id, "to_agent_id": to_agent_id},
        )

    # 4. INVALID_CATEGORY
    if category not in VALID_CATEGORIES:
        return ValidationError(
            error="INVALID_CATEGORY",
            message=f"Category must be one of: {', '.join(sorted(VALID_CATEGORIES))}",
            status_code=400,
            details={"category": category, "valid_categories": sorted(VALID_CATEGORIES)},
        )

    # 5. INVALID_RATING
    if rating not in VALID_RATINGS:
        return ValidationError(
            error="INVALID_RATING",
            message=f"Rating must be one of: {', '.join(sorted(VALID_RATINGS))}",
            status_code=400,
            details={"rating": rating, "valid_ratings": sorted(VALID_RATINGS)},
        )

    # 6. COMMENT_TOO_LONG
    comment = body.get("comment")
    if comment is not None and isinstance(comment, str) and len(comment) > max_comment_length:
        return ValidationError(
            error="COMMENT_TOO_LONG",
            message=f"Comment exceeds maximum length of {max_comment_length} codepoints",
            status_code=400,
            details={"max_length": max_comment_length, "actual_length": len(comment)},
        )

    return None


def submit_feedback(
    store: FeedbackStore,
    body: dict[str, object],
    max_comment_length: int,
) -> FeedbackRecord | ValidationError:
    """
    Validate and store a feedback submission.

    Returns FeedbackRecord on success, ValidationError on failure.
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
        )
    except DuplicateFeedbackError:
        return ValidationError(
            error="FEEDBACK_EXISTS",
            message="Feedback already submitted for this task, from_agent, to_agent combination",
            status_code=409,
            details={
                "task_id": task_id,
                "from_agent_id": from_agent_id,
                "to_agent_id": to_agent_id,
            },
        )

    return record


def is_visible(record: FeedbackRecord, reveal_timeout_seconds: int) -> bool:
    """Check if a feedback record is visible (revealed or timed out)."""
    if record.visible:
        return True

    # Check timeout
    submitted = datetime.fromisoformat(record.submitted_at)
    elapsed = (datetime.now(UTC) - submitted).total_seconds()
    return elapsed >= reveal_timeout_seconds


def get_feedback_by_id(
    store: FeedbackStore,
    feedback_id: str,
    reveal_timeout_seconds: int,
) -> FeedbackRecord | None:
    """Get a feedback record by ID, returning None if not found or sealed."""
    record = store.get_by_id(feedback_id)
    if record is None:
        return None
    if not is_visible(record, reveal_timeout_seconds):
        return None
    return record


def get_feedback_for_task(
    store: FeedbackStore,
    task_id: str,
    reveal_timeout_seconds: int,
) -> list[FeedbackRecord]:
    """Get all visible feedback for a task, sorted by submitted_at."""
    records = store.get_by_task(task_id)
    return [r for r in records if is_visible(r, reveal_timeout_seconds)]


def get_feedback_for_agent(
    store: FeedbackStore,
    agent_id: str,
    reveal_timeout_seconds: int,
) -> list[FeedbackRecord]:
    """Get all visible feedback about an agent (to_agent_id), sorted by submitted_at."""
    records = store.get_by_agent(agent_id)
    return [r for r in records if is_visible(r, reveal_timeout_seconds)]

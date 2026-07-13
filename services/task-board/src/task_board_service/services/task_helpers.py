"""Shared formatting/validation helpers used by every TaskManager coordinator.

Extracted verbatim from task_manager.py (WP-11, B16 god-class decomposition) —
pure functions with no dependency on TaskManager or any coordinator, so every
coordinator module can import them without a circular import.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from task_board_service.services.deadline_evaluator import DeadlineEvaluator

# Regex for task_id format: t-<uuid4>
TASK_ID_RE = re.compile(
    r"^t-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

# Valid task statuses
VALID_STATUSES = frozenset(
    {"open", "accepted", "submitted", "approved", "cancelled", "disputed", "ruled", "expired"}
)


def now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def is_positive_int(value: object) -> bool:
    """Check if value is a positive integer (not float, not bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def is_valid_worker_pct(value: object) -> bool:
    """Check if value is an integer 0-100 (not float, not bool)."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100


def task_to_response(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB row dict to a full task response dict."""
    bidding_deadline = DeadlineEvaluator.compute_deadline(
        row["created_at"], row["bidding_deadline_seconds"]
    )
    execution_deadline = DeadlineEvaluator.compute_deadline(
        row["accepted_at"], row["deadline_seconds"]
    )
    review_deadline = DeadlineEvaluator.compute_deadline(
        row["submitted_at"], row["review_deadline_seconds"]
    )
    return {
        "task_id": row["task_id"],
        "poster_id": row["poster_id"],
        "title": row["title"],
        "spec": row["spec"],
        "reward": row["reward"],
        "bidding_deadline_seconds": row["bidding_deadline_seconds"],
        "deadline_seconds": row["deadline_seconds"],
        "review_deadline_seconds": row["review_deadline_seconds"],
        "status": row["status"],
        "escrow_id": row["escrow_id"],
        "bid_count": row["bid_count"],
        "worker_id": row["worker_id"],
        "accepted_bid_id": row["accepted_bid_id"],
        "created_at": row["created_at"],
        "accepted_at": row["accepted_at"],
        "submitted_at": row["submitted_at"],
        "approved_at": row["approved_at"],
        "cancelled_at": row["cancelled_at"],
        "disputed_at": row["disputed_at"],
        "dispute_reason": row["dispute_reason"],
        "dispute_id": row["dispute_id"],
        "ruling_id": row["ruling_id"],
        "ruled_at": row["ruled_at"],
        "worker_pct": row["worker_pct"],
        "ruling_summary": row["ruling_summary"],
        "expired_at": row["expired_at"],
        "escrow_pending": bool(row["escrow_pending"]),
        "bidding_deadline": bidding_deadline,
        "execution_deadline": execution_deadline,
        "review_deadline": review_deadline,
    }


def task_to_summary(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB row dict to a summary dict for list views."""
    bidding_deadline = DeadlineEvaluator.compute_deadline(
        row["created_at"], row["bidding_deadline_seconds"]
    )
    execution_deadline = DeadlineEvaluator.compute_deadline(
        row["accepted_at"], row["deadline_seconds"]
    )
    review_deadline = DeadlineEvaluator.compute_deadline(
        row["submitted_at"], row["review_deadline_seconds"]
    )
    return {
        "task_id": row["task_id"],
        "poster_id": row["poster_id"],
        "title": row["title"],
        "reward": row["reward"],
        "status": row["status"],
        "bid_count": row["bid_count"],
        "worker_id": row["worker_id"],
        "created_at": row["created_at"],
        "bidding_deadline": bidding_deadline,
        "execution_deadline": execution_deadline,
        "review_deadline": review_deadline,
    }

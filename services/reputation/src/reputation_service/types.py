"""Shared lightweight data types for reputation service layers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FeedbackRecord:
    """A single feedback record."""

    feedback_id: str
    task_id: str
    from_agent_id: str
    to_agent_id: str
    category: str
    rating: str
    comment: str | None
    submitted_at: str
    visible: bool

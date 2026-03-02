"""Backward-compatible FeedbackStore import shim."""

from reputation_service.services.exceptions import DuplicateFeedbackError
from reputation_service.services.sqlite_feedback_store import SqliteFeedbackStore

FeedbackStore = SqliteFeedbackStore

__all__ = ["DuplicateFeedbackError", "FeedbackStore", "SqliteFeedbackStore"]

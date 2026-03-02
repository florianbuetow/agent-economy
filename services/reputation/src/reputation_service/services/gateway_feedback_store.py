"""Backward-compatibility shim for legacy GatewayFeedbackStore imports."""

from reputation_service.services.feedback_db_client import FeedbackDbClient as GatewayFeedbackStore

__all__ = ["GatewayFeedbackStore"]

"""Task store domain exceptions shared across storage implementations."""


class DuplicateTaskError(Exception):
    """Raised when attempting to insert a task with a duplicate task_id."""


class DuplicateBidError(Exception):
    """Raised when attempting to insert a duplicate bid for a task/bidder pair."""

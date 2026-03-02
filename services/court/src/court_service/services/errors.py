"""Court dispute store domain exceptions."""


class DuplicateDisputeError(Exception):
    """Raised when attempting to create a second dispute for the same task."""

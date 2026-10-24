"""Task store domain exceptions shared across storage implementations."""


class DuplicateTaskError(Exception):
    """Raised when attempting to insert a task with a duplicate task_id."""


class DuplicateBidError(Exception):
    """Raised when attempting to insert a duplicate bid for a task/bidder pair."""


class AssetInsertRejectedError(RuntimeError):
    """Raised when the Gateway definitively rejects asset metadata before commit."""

    def __init__(
        self,
        status_code: int,
        error: str | None,
        message: str,
        details: dict[str, object],
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message
        self.details = details

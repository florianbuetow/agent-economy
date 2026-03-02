"""Storage protocol for Reputation service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from reputation_service.types import FeedbackRecord


class FeedbackStorageInterface(Protocol):
    """Protocol defining the Reputation storage interface."""

    def insert_feedback(
        self,
        task_id: str,
        from_agent_id: str,
        to_agent_id: str,
        category: str,
        rating: str,
        comment: str | None,
        *,
        force_visible: bool,
    ) -> FeedbackRecord: ...

    def get_by_id(self, feedback_id: str) -> FeedbackRecord | None: ...

    def get_by_task(self, task_id: str) -> list[FeedbackRecord]: ...

    def get_by_agent(self, agent_id: str) -> list[FeedbackRecord]: ...

    def count(self) -> int: ...

    def close(self) -> None: ...

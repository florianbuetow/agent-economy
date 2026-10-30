"""Storage protocol for Reputation service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from service_auth.jws import VerifiedJws

    from reputation_service.services.task_board_client import TaskParties
    from reputation_service.types import FeedbackRecord


class JwsVerifier(Protocol):
    """Protocol for verifying a JWS token.

    Both the Identity HTTP client (agent operations) and the local platform verifier
    (platform operations) implement this, so the feedback router can route by signer
    without depending on either concrete class.
    """

    async def verify_jws(self, token: str) -> VerifiedJws: ...

    async def close(self) -> None: ...


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
        source: str,
    ) -> FeedbackRecord: ...

    def get_by_id(self, feedback_id: str) -> FeedbackRecord | None: ...

    def get_by_task(self, task_id: str) -> list[FeedbackRecord]: ...

    def get_by_agent(self, agent_id: str) -> list[FeedbackRecord]: ...

    def reveal_expired(self, cutoff: str) -> int:
        """Durably reveal sealed feedback submitted at or before ``cutoff``.

        Returns the number of rows revealed; each newly revealed pair emits one
        ``feedback.revealed`` event.
        """
        ...

    def count(self) -> int: ...

    def close(self) -> None: ...


class TaskLookup(Protocol):
    """Protocol for reading the task facts that authorize peer feedback."""

    async def get_task(self, task_id: str) -> TaskParties: ...

    async def close(self) -> None: ...

"""Storage protocol for Reputation service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from reputation_service.types import FeedbackRecord


class JwsVerifier(Protocol):
    """Protocol for verifying a JWS token.

    Both the Identity HTTP client (agent operations) and the local platform verifier
    (platform operations) implement this, so the feedback router can route by signer
    without depending on either concrete class.
    """

    async def verify_jws(self, token: str) -> dict[str, Any]:
        """Return ``{"valid": bool, "agent_id": str, "payload": dict}``."""
        ...

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
    ) -> FeedbackRecord: ...

    def get_by_id(self, feedback_id: str) -> FeedbackRecord | None: ...

    def get_by_task(self, task_id: str) -> list[FeedbackRecord]: ...

    def get_by_agent(self, agent_id: str) -> list[FeedbackRecord]: ...

    def count(self) -> int: ...

    def close(self) -> None: ...

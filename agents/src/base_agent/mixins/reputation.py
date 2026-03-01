"""Reputation mixin — feedback submission and retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


class _ReputationClient(Protocol):
    config: AgentConfig
    agent_id: str | None

    def _sign_jws(self, payload: dict[str, object]) -> str: ...

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...


class ReputationMixin:
    """Methods for interacting with the Reputation service (port 8004)."""

    async def submit_feedback(
        self: _ReputationClient,
        task_id: str,
        to_agent_id: str,
        category: str,
        rating: str,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Submit feedback about another agent."""
        url = f"{self.config.reputation_url}/feedback"
        payload: dict[str, object] = {
            "action": "submit_feedback",
            "from_agent_id": self.agent_id,
            "to_agent_id": to_agent_id,
            "task_id": task_id,
            "category": category,
            "rating": rating,
        }
        if comment is not None:
            payload["comment"] = comment
        token = self._sign_jws(payload)
        return await self._request("POST", url, json={"token": token})

    async def get_task_feedback(self: _ReputationClient, task_id: str) -> list[dict[str, Any]]:
        """Get all feedback for a task."""
        url = f"{self.config.reputation_url}/feedback/task/{task_id}"
        response = await self._request("GET", url)
        return cast("list[dict[str, Any]]", response["feedback"])

    async def get_agent_feedback(self: _ReputationClient, agent_id: str) -> list[dict[str, Any]]:
        """Get all feedback about an agent."""
        url = f"{self.config.reputation_url}/feedback/agent/{agent_id}"
        response = await self._request("GET", url)
        return cast("list[dict[str, Any]]", response["feedback"])

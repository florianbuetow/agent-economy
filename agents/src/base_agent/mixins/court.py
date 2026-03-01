"""Court mixin — dispute filing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


class _CourtClient(Protocol):
    config: AgentConfig
    agent_id: str | None

    def _sign_jws(self, payload: dict[str, object]) -> str: ...

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...


class CourtMixin:
    """Methods for interacting with the Court service (port 8005)."""

    async def file_claim(self: _CourtClient, task_id: str, reason: str) -> dict[str, Any]:
        """File a claim with the Court."""
        url = f"{self.config.court_url}/disputes/file"
        token = self._sign_jws(
            {
                "action": "file_dispute",
                "task_id": task_id,
                "claimant_id": self.agent_id,
                "claim": reason,
            }
        )
        return await self._request("POST", url, json={"token": token})

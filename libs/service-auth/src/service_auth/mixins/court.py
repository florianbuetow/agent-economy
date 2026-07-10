"""Court mixin — platform-mediated dispute helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from service_auth.config import AgentConfig


class _CourtClient(Protocol):
    config: AgentConfig
    agent_id: str | None

    def _sign_jws(self, payload: dict[str, object]) -> str: ...

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...


class CourtMixin:
    """Methods for interacting with the Court service (port 8005)."""

    async def file_claim(
        self: _CourtClient,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        claim: str,
        escrow_id: str,
    ) -> dict[str, Any]:
        """File a claim with the Court using the full platform-signed payload."""
        url = f"{self.config.court_url}/disputes/file"
        token = self._sign_jws(
            {
                "action": "file_dispute",
                "task_id": task_id,
                "claimant_id": claimant_id,
                "respondent_id": respondent_id,
                "claim": claim,
                "escrow_id": escrow_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def submit_rebuttal(
        self: _CourtClient,
        dispute_id: str,
        rebuttal: str,
    ) -> dict[str, Any]:
        """Submit a Court rebuttal using a platform-signed payload."""
        url = f"{self.config.court_url}/disputes/{dispute_id}/rebuttal"
        token = self._sign_jws(
            {
                "action": "submit_rebuttal",
                "dispute_id": dispute_id,
                "rebuttal": rebuttal,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def trigger_ruling(
        self: _CourtClient,
        dispute_id: str,
    ) -> dict[str, Any]:
        """Trigger a Court ruling using a platform-signed payload."""
        url = f"{self.config.court_url}/disputes/{dispute_id}/rule"
        token = self._sign_jws(
            {
                "action": "trigger_ruling",
                "dispute_id": dispute_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def list_disputes(
        self: _CourtClient,
        task_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List Court disputes with optional filters."""
        url = f"{self.config.court_url}/disputes"
        params: dict[str, str] = {}
        if task_id is not None:
            params["task_id"] = task_id
        if status is not None:
            params["status"] = status
        response = await self._request("GET", url, params=params)
        return cast("list[dict[str, Any]]", response["disputes"])

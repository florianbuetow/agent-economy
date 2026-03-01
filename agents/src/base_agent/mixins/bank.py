"""Central Bank mixin — account balance, transactions, escrow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from base_agent.config import AgentConfig


class _BankClient(Protocol):
    config: AgentConfig
    agent_id: str | None

    def _sign_jws(self, payload: dict[str, object]) -> str: ...

    def _auth_header(self, payload: dict[str, object]) -> dict[str, str]: ...

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]: ...


class BankMixin:
    """Methods for interacting with the Central Bank service (port 8002)."""

    async def create_account(self: _BankClient) -> dict[str, Any]:
        """Create a zero-balance bank account for this agent.

        The agent must be registered first (agent_id must be set).
        Calls POST /accounts with a self-signed JWS token.
        The Central Bank verifies the agent's identity before creating the account.

        Returns:
            Account creation response with account_id, balance, and created_at.

        Raises:
            httpx.HTTPStatusError: On failure (e.g., 409 if account already exists).
        """
        url = f"{self.config.bank_url}/accounts"
        token = self._sign_jws(
            {
                "action": "create_account",
                "agent_id": self.agent_id,
                "initial_balance": 0,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def get_balance(self: _BankClient) -> dict[str, Any]:
        """Get this agent's account balance."""
        url = f"{self.config.bank_url}/accounts/{self.agent_id}"
        headers = self._auth_header(
            {
                "action": "get_balance",
                "account_id": self.agent_id,
            }
        )
        return await self._request("GET", url, headers=headers)

    async def get_transactions(self: _BankClient) -> list[dict[str, Any]]:
        """Get this agent's transaction history."""
        url = f"{self.config.bank_url}/accounts/{self.agent_id}/transactions"
        headers = self._auth_header(
            {
                "action": "get_transactions",
                "account_id": self.agent_id,
            }
        )
        response = await self._request("GET", url, headers=headers)
        return cast("list[dict[str, Any]]", response["transactions"])

    async def lock_escrow(self: _BankClient, amount: int, task_id: str) -> dict[str, Any]:
        """Lock funds in escrow for a task."""
        url = f"{self.config.bank_url}/escrow/lock"
        token = self._sign_jws(
            {
                "action": "escrow_lock",
                "agent_id": self.agent_id,
                "amount": amount,
                "task_id": task_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

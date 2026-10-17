"""Central Bank mixin — account balance, transactions, escrow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from service_auth.financial_responses import (
    parse_account_projection,
    parse_native_financial_envelope,
    validate_operation_id,
)

if TYPE_CHECKING:
    from service_auth.config import AgentConfig


class _BankClient(Protocol):
    config: AgentConfig
    agent_id: str | None

    def _sign_jws(self, payload: dict[str, object], *, audience: str, resource: str) -> str: ...

    def _auth_header(
        self, payload: dict[str, object], *, audience: str, resource: str
    ) -> dict[str, str]: ...

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
        if self.agent_id is None:
            raise RuntimeError("Agent must be registered before creating an account")
        url = f"{self.config.bank_url}/accounts"
        token = self._sign_jws(
            {
                "action": "create_account",
                "agent_id": self.agent_id,
                "initial_balance": 0,
            },
            audience="central-bank",
            resource="POST /accounts",
        )
        response = await self._request("POST", url, json={"token": token})
        return parse_account_projection(response, self.agent_id)

    async def get_balance(self: _BankClient) -> dict[str, Any]:
        """Get this agent's account balance."""
        url = f"{self.config.bank_url}/accounts/{self.agent_id}"
        headers = self._auth_header(
            {
                "action": "get_balance",
                "account_id": self.agent_id,
            },
            audience="central-bank",
            resource=f"GET /accounts/{self.agent_id}",
        )
        return await self._request("GET", url, headers=headers)

    async def get_transactions(self: _BankClient) -> list[dict[str, Any]]:
        """Get this agent's transaction history."""
        url = f"{self.config.bank_url}/accounts/{self.agent_id}/transactions"
        headers = self._auth_header(
            {
                "action": "get_transactions",
                "account_id": self.agent_id,
            },
            audience="central-bank",
            resource=f"GET /accounts/{self.agent_id}/transactions",
        )
        response = await self._request("GET", url, headers=headers)
        return cast("list[dict[str, Any]]", response["transactions"])

    async def lock_escrow(
        self: _BankClient, amount: int, task_id: str, *, operation_id: str
    ) -> dict[str, Any]:
        """Lock funds in escrow for a task."""
        validate_operation_id(operation_id)
        url = f"{self.config.bank_url}/escrow/lock"
        token = self._sign_jws(
            {
                "action": "escrow_lock",
                "operation_id": operation_id,
                "agent_id": self.agent_id,
                "amount": amount,
                "task_id": task_id,
            },
            audience="central-bank",
            resource="POST /escrow/lock",
        )
        response = await self._request("POST", url, json={"token": token})
        return parse_native_financial_envelope(
            response,
            operation_id,
            "escrow_lock",
            {"payer_account_id": self.agent_id, "task_id": task_id, "amount": amount},
        )

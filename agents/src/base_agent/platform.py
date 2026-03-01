"""PlatformAgent — privileged agent for platform banking operations."""

from __future__ import annotations

from typing import Any

from base_agent.agent import BaseAgent
from base_agent.signing import verify_jws


class PlatformAgent(BaseAgent):
    """Privileged platform agent for system operations.

    Extends BaseAgent with methods for operations that only the platform
    agent is authorized to perform: creating accounts, crediting funds,
    and managing escrow releases/splits.

    Also provides local JWS verification so services can validate incoming
    platform-signed requests without calling the Identity service.
    """

    async def create_account(self, agent_id: str, initial_balance: int) -> dict[str, Any]:
        """Create an account for an agent in the Central Bank.

        Args:
            agent_id: The agent to create an account for.
            initial_balance: Starting balance for the account.

        Returns:
            Account creation response from Central Bank.
        """
        url = f"{self.config.bank_url}/accounts"
        token = self._sign_jws(
            {"action": "create_account", "agent_id": agent_id, "initial_balance": initial_balance}
        )
        return await self._request("POST", url, json={"token": token})

    async def credit_account(
        self, account_id: str, amount: int, reference: str
    ) -> dict[str, Any]:
        """Credit funds to an account.

        Args:
            account_id: The account to credit.
            amount: Amount to credit (positive integer).
            reference: Reference string for the transaction.

        Returns:
            Credit response from Central Bank.
        """
        url = f"{self.config.bank_url}/accounts/{account_id}/credit"
        token = self._sign_jws(
            {
                "action": "credit",
                "account_id": account_id,
                "amount": amount,
                "reference": reference,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def release_escrow(
        self, escrow_id: str, recipient_account_id: str
    ) -> dict[str, Any]:
        """Release escrowed funds to recipient.

        Args:
            escrow_id: The escrow to release.
            recipient_account_id: Account to receive the funds.

        Returns:
            Release response from Central Bank.
        """
        url = f"{self.config.bank_url}/escrow/{escrow_id}/release"
        token = self._sign_jws(
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
            }
        )
        return await self._request("POST", url, json={"token": token})

    async def split_escrow(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
    ) -> dict[str, Any]:
        """Split escrowed funds between worker and poster.

        Args:
            escrow_id: The escrow to split.
            worker_account_id: Worker's account.
            poster_account_id: Poster's account.
            worker_pct: Percentage (0-100) going to the worker.

        Returns:
            Split response from Central Bank.
        """
        url = f"{self.config.bank_url}/escrow/{escrow_id}/split"
        token = self._sign_jws(
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
            }
        )
        return await self._request("POST", url, json={"token": token})

    def verify_platform_jws(self, token: str) -> dict[str, object]:
        """Verify a JWS token was signed by this platform agent.

        Uses local cryptographic verification against this agent's public key.
        No Identity service round-trip needed.

        Args:
            token: Compact JWS string to verify.

        Returns:
            Decoded payload as a dictionary.

        Raises:
            cryptography.exceptions.InvalidSignature: If the signature is invalid.
            ValueError: If the token format is invalid.
        """
        return verify_jws(token, self._public_key)

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"PlatformAgent(name={self.name!r}{registered})"

"""PlatformAgent — privileged agent for platform operations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_auth.agent import BaseAgent
from service_auth.financial_responses import (
    parse_account_projection,
    parse_native_financial_envelope,
    validate_operation_id,
)

if TYPE_CHECKING:
    from service_auth.jws import VerifiedJws


class PlatformAgent(BaseAgent):
    """Privileged platform agent for system operations.

    Extends BaseAgent with methods for operations that only the platform
    agent is authorized to perform: creating accounts, crediting funds,
    and managing escrow releases/splits.

    Also provides local JWS verification so services can validate incoming
    platform-signed requests without calling the Identity service.
    """

    def sign(
        self,
        payload: dict[str, Any],
        *,
        audience: str,
        resource: str,
    ) -> str:
        """Sign a token with this registered platform identity's in-memory key."""
        if self.agent_id is None:
            msg = "Platform agent must be registered before signing"
            raise RuntimeError(msg)
        return self._sign_jws(payload, audience=audience, resource=resource)

    async def create_account(  # type: ignore[override]
        self,
        agent_id: str,
        initial_balance: int,
        *,
        operation_id: str | None,
    ) -> dict[str, Any]:
        """Create an account for an agent in the Central Bank.

        Args:
            agent_id: The agent to create an account for.
            initial_balance: Starting balance for the account.
            operation_id: Caller-owned issuance identity; None for a zero-balance account.

        Returns:
            Account creation response from Central Bank.
        """
        if initial_balance > 0 and operation_id is None:
            raise ValueError("Positive issuance requires operation_id")
        if initial_balance == 0 and operation_id is not None:
            raise ValueError("Zero-balance creation requires operation_id=None")
        payload: dict[str, Any] = {
            "action": "create_account",
            "agent_id": agent_id,
            "initial_balance": initial_balance,
        }
        if operation_id is not None:
            validate_operation_id(operation_id)
            payload["operation_id"] = operation_id
        url = f"{self.config.bank_url}/accounts"
        token = self._sign_jws(
            payload,
            audience="central-bank",
            resource="POST /accounts",
        )
        response = await self._request("POST", url, json={"token": token})
        if operation_id is None:
            return parse_account_projection(response, agent_id)
        return parse_native_financial_envelope(
            response,
            operation_id,
            "account_issuance",
            {"account_id": agent_id, "amount": initial_balance, "reference": "initial_balance"},
        )

    async def credit_account(
        self,
        account_id: str,
        amount: int,
        reference: str,
        *,
        operation_id: str,
    ) -> dict[str, Any]:
        """Credit funds to an account.

        Args:
            account_id: The account to credit.
            amount: Amount to credit (positive integer).
            reference: Reference string for the transaction.
            operation_id: Caller-owned identity reused for every retry of this credit.

        Returns:
            Credit response from Central Bank.
        """
        validate_operation_id(operation_id)
        url = f"{self.config.bank_url}/accounts/{account_id}/credit"
        token = self._sign_jws(
            {
                "action": "credit",
                "operation_id": operation_id,
                "account_id": account_id,
                "amount": amount,
                "reference": reference,
            },
            audience="central-bank",
            resource=f"POST /accounts/{account_id}/credit",
        )
        response = await self._request("POST", url, json={"token": token})
        return parse_native_financial_envelope(
            response,
            operation_id,
            "credit",
            {"account_id": account_id, "amount": amount, "reference": reference},
        )

    async def release_escrow(
        self,
        escrow_id: str,
        recipient_account_id: str,
        *,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        """Release escrowed funds to recipient.

        Args:
            escrow_id: The escrow to release.
            recipient_account_id: Account to receive the funds.
            operation_id: Caller-owned identity reused for every retry of this release.
            source_reference: Stable business reference for the settlement.

        Returns:
            Release response from Central Bank.
        """
        validate_operation_id(operation_id)
        url = f"{self.config.bank_url}/escrow/{escrow_id}/release"
        token = self._sign_jws(
            {
                "action": "escrow_release",
                "operation_id": operation_id,
                "source_reference": source_reference,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
            },
            audience="central-bank",
            resource=f"POST /escrow/{escrow_id}/release",
        )
        response = await self._request("POST", url, json={"token": token})
        return parse_native_financial_envelope(
            response,
            operation_id,
            "escrow_release",
            {
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
                "source_reference": source_reference,
            },
        )

    async def split_escrow(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
        *,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        """Split escrowed funds between worker and poster.

        Args:
            escrow_id: The escrow to split.
            worker_account_id: Worker's account.
            poster_account_id: Poster's account.
            worker_pct: Percentage (0-100) going to the worker.
            operation_id: Caller-owned identity reused for every retry of this split.
            source_reference: Stable business reference for the settlement.

        Returns:
            Split response from Central Bank.
        """
        validate_operation_id(operation_id)
        url = f"{self.config.bank_url}/escrow/{escrow_id}/split"
        token = self._sign_jws(
            {
                "action": "escrow_split",
                "operation_id": operation_id,
                "source_reference": source_reference,
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
            },
            audience="central-bank",
            resource=f"POST /escrow/{escrow_id}/split",
        )
        response = await self._request("POST", url, json={"token": token})
        return parse_native_financial_envelope(
            response,
            operation_id,
            "escrow_split",
            {
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
                "source_reference": source_reference,
            },
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """Fetch task details from the Task Board.

        Args:
            task_id: The task to fetch.

        Returns:
            Task data dictionary from Task Board.
        """
        url = f"{self.config.task_board_url}/tasks/{task_id}"
        return await self._request("GET", url)

    async def record_ruling(self, task_id: str, ruling_payload: dict[str, Any]) -> dict[str, Any]:
        """Record a dispute ruling on the Task Board.

        Args:
            task_id: The task the ruling applies to.
            ruling_payload: Ruling data to sign and send.

        Returns:
            Response from Task Board.
        """
        url = f"{self.config.task_board_url}/tasks/{task_id}/ruling"
        token = self._sign_jws(
            ruling_payload,
            audience="task-board",
            resource=f"POST /tasks/{task_id}/ruling",
        )
        return await self._request("POST", url, json={"token": token})

    async def submit_platform_feedback(self, feedback_payload: dict[str, Any]) -> dict[str, Any]:
        """Submit reputation feedback as the platform agent.

        Args:
            feedback_payload: Feedback data to sign and send.

        Returns:
            Response from Reputation service.
        """
        url = f"{self.config.reputation_url}/feedback"
        token = self._sign_jws(
            feedback_payload,
            audience="reputation",
            resource="POST /feedback",
        )
        return await self._request("POST", url, json={"token": token})

    def verify_platform_jws(self, token: str) -> VerifiedJws:
        """Verify a JWS token was signed by this platform agent.

        Alias for validate_certificate(). Prefer validate_certificate() for new code.
        """
        return self.validate_certificate(token)

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"PlatformAgent(name={self.name!r}{registered})"

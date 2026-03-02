"""DB Gateway client for Central Bank write operations."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from service_clients.base import BaseServiceClient


class GatewayClient(BaseServiceClient):
    """Async HTTP client for DB Gateway bank endpoints."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            service_name="db_gateway",
        )

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _build_event(
        self,
        event_type: str,
        summary: str,
        payload: dict[str, Any],
        *,
        task_id: str | None,
        agent_id: str | None,
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "event_source": "bank",
            "event_type": event_type,
            "timestamp": self._now(),
            "summary": summary,
            "payload": json.dumps(payload),
        }
        if task_id is not None:
            event["task_id"] = task_id
        if agent_id is not None:
            event["agent_id"] = agent_id
        return event

    async def create_account(
        self,
        account_id: str,
        created_at: str,
        balance: int,
        initial_credit_data: dict[str, Any] | None,
        agent_name: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "account_id": account_id,
            "created_at": created_at,
            "balance": balance,
            "event": self._build_event(
                event_type="account.created",
                summary=f"Created account for {agent_name}",
                payload={"agent_name": agent_name},
                task_id=None,
                agent_id=account_id,
            ),
        }
        if initial_credit_data is not None:
            payload["initial_credit"] = initial_credit_data
        return await self._post("/bank/accounts", payload, expected_status=201)

    async def credit_account(
        self,
        tx_id: str,
        account_id: str,
        amount: int,
        reference: str,
        timestamp: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tx_id": tx_id,
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
            "timestamp": timestamp,
            "event": self._build_event(
                event_type="salary.paid",
                summary=f"Paid {amount} credits to {account_id}",
                payload={"amount": amount},
                task_id=None,
                agent_id=account_id,
            ),
        }
        return await self._post("/bank/credit", payload, expected_status=200)

    async def escrow_lock(
        self,
        escrow_id: str,
        payer_account_id: str,
        amount: int,
        task_id: str,
        created_at: str,
        tx_id: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "payer_account_id": payer_account_id,
            "amount": amount,
            "task_id": task_id,
            "created_at": created_at,
            "tx_id": tx_id,
            "event": self._build_event(
                event_type="escrow.locked",
                summary=f"Locked {amount} credits in escrow for {task_id}",
                payload={"escrow_id": escrow_id, "amount": amount, "title": task_id},
                task_id=task_id,
                agent_id=payer_account_id,
            ),
        }
        return await self._post("/bank/escrow/lock", payload, expected_status=201)

    async def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
        tx_id: str,
        resolved_at: str,
        amount: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_account_id,
            "tx_id": tx_id,
            "resolved_at": resolved_at,
            "event": self._build_event(
                event_type="escrow.released",
                summary=f"Released {amount} credits from escrow {escrow_id}",
                payload={
                    "escrow_id": escrow_id,
                    "amount": amount,
                    "recipient_id": recipient_account_id,
                    "recipient_name": recipient_account_id,
                },
                task_id=None,
                agent_id=recipient_account_id,
            ),
        }
        return await self._post("/bank/escrow/release", payload, expected_status=200)

    async def escrow_split(
        self,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_tx_id: str,
        poster_tx_id: str,
        resolved_at: str,
        worker_amount: int,
        poster_amount: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "worker_account_id": worker_account_id,
            "poster_account_id": poster_account_id,
            "worker_tx_id": worker_tx_id,
            "poster_tx_id": poster_tx_id,
            "resolved_at": resolved_at,
            "worker_amount": worker_amount,
            "poster_amount": poster_amount,
            "event": self._build_event(
                event_type="escrow.split",
                summary=f"Split escrow {escrow_id}: {worker_amount}/{poster_amount}",
                payload={
                    "escrow_id": escrow_id,
                    "worker_amount": worker_amount,
                    "poster_amount": poster_amount,
                },
                task_id=None,
                agent_id=worker_account_id,
            ),
        }
        return await self._post("/bank/escrow/split", payload, expected_status=200)

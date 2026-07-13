"""DB Gateway client — the single async HTTP client for db-gateway's HTTP API.

Covers every route identity, central-bank, reputation, and court actually use
(task-board is excluded — WP-05 owns task-board's client swap). Each method's
status-code-to-ServiceError mapping mirrors what the four services' own
`*_db_client.py` files did before this consolidation, so swapping a service's
client to delegate here preserves its exact observable error codes/status
codes. Connection failures (refused connection, timeout) are mapped once, here,
to a uniform 502 `db_gateway_unavailable` — none of the four hand-rolled clients
did this consistently before, so a downed gateway used to surface as an
unhandled exception instead of a clean 502.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, NoReturn

from service_clients.base import BaseServiceClient


class GatewayClient(BaseServiceClient):
    """Async HTTP client for DB Gateway endpoints."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            service_name="db_gateway",
        )

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _raise_status_error(self, path: str, response: Any) -> NoReturn:
        raise self._status_error(path, response)

    # ------------------------------------------------------------------
    # Identity — /identity/agents
    # ------------------------------------------------------------------

    def build_agent_registered_event(
        self, agent_id: str, name: str, timestamp: str
    ) -> dict[str, Any]:
        return {
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": timestamp,
            "agent_id": agent_id,
            "summary": f"{name} registered as agent",
            "payload": json.dumps({"agent_name": name}),
        }

    async def register_agent(
        self,
        agent_id: str,
        name: str,
        public_key: str,
        registered_at: str,
    ) -> dict[str, Any]:
        """POST /identity/agents. Raises ServiceError(error='public_key_exists', 409)."""
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "name": name,
            "public_key": public_key,
            "registered_at": registered_at,
            "event": self.build_agent_registered_event(agent_id, name, registered_at),
        }
        response = await self._post_raw("/identity/agents", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        self._raise_status_error("/identity/agents", response)

    async def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """GET /identity/agents/{agent_id}. Returns None on 404."""
        return await self._get(
            f"/identity/agents/{agent_id}",
            expected_status=200,
            not_found_returns_none=True,
        )

    async def list_agents(self) -> list[dict[str, Any]]:
        """GET /identity/agents."""
        response = await self._get_raw("/identity/agents")
        if response.status_code != 200:
            self._raise_status_error("/identity/agents", response)
        body = self._response_dict(response)
        agents = body.get("agents", [])
        return [a for a in agents if isinstance(a, dict)] if isinstance(agents, list) else []

    async def count_agents(self) -> int:
        """GET /identity/agents/count."""
        response = await self._get_raw("/identity/agents/count")
        if response.status_code != 200:
            self._raise_status_error("/identity/agents/count", response)
        return int(self._response_dict(response).get("count", 0))

    # ------------------------------------------------------------------
    # Bank — /bank/accounts, /bank/credit, /bank/escrow
    # ------------------------------------------------------------------

    def _build_bank_event(
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
        """POST /bank/accounts. Raises ServiceError(error='account_exists', 409) on conflict."""
        payload: dict[str, Any] = {
            "account_id": account_id,
            "created_at": created_at,
            "balance": balance,
            "event": self._build_bank_event(
                event_type="account.created",
                summary=f"Created account for {agent_name}",
                payload={"agent_name": agent_name},
                task_id=None,
                agent_id=account_id,
            ),
        }
        if initial_credit_data is not None:
            payload["initial_credit"] = initial_credit_data
        response = await self._post_raw("/bank/accounts", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        self._raise_status_error("/bank/accounts", response)

    async def get_account(self, account_id: str) -> dict[str, Any] | None:
        """GET /bank/accounts/{account_id}. Returns None on 404."""
        return await self._get(
            f"/bank/accounts/{account_id}",
            expected_status=200,
            not_found_returns_none=True,
        )

    async def get_transactions(self, account_id: str) -> list[dict[str, Any]]:
        """GET /bank/accounts/{account_id}/transactions."""
        response = await self._get_raw(f"/bank/accounts/{account_id}/transactions")
        if response.status_code != 200:
            self._raise_status_error(f"/bank/accounts/{account_id}/transactions", response)
        items = self._response_dict(response).get("transactions", [])
        return [t for t in items if isinstance(t, dict)] if isinstance(items, list) else []

    async def count_accounts(self) -> int:
        """GET /bank/accounts/count."""
        response = await self._get_raw("/bank/accounts/count")
        if response.status_code != 200:
            self._raise_status_error("/bank/accounts/count", response)
        return int(self._response_dict(response).get("count", 0))

    async def total_escrowed(self) -> int:
        """GET /bank/escrow/total-locked."""
        response = await self._get_raw("/bank/escrow/total-locked")
        if response.status_code != 200:
            self._raise_status_error("/bank/escrow/total-locked", response)
        return int(self._response_dict(response).get("total", 0))

    async def get_escrow(self, escrow_id: str) -> dict[str, Any] | None:
        """GET /bank/escrow/{escrow_id}. Returns None on 404."""
        return await self._get(
            f"/bank/escrow/{escrow_id}",
            expected_status=200,
            not_found_returns_none=True,
        )

    async def credit_account(
        self,
        tx_id: str,
        account_id: str,
        amount: int,
        reference: str,
        timestamp: str,
    ) -> dict[str, Any]:
        """POST /bank/credit."""
        payload: dict[str, Any] = {
            "tx_id": tx_id,
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
            "timestamp": timestamp,
            "event": self._build_bank_event(
                event_type="salary.paid",
                summary=f"Paid {amount} credits to {account_id}",
                payload={"amount": amount},
                task_id=None,
                agent_id=account_id,
            ),
        }
        response = await self._post_raw("/bank/credit", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        self._raise_status_error("/bank/credit", response)

    async def escrow_lock(
        self,
        escrow_id: str,
        payer_account_id: str,
        amount: int,
        task_id: str,
        created_at: str,
        tx_id: str,
    ) -> dict[str, Any]:
        """POST /bank/escrow/lock."""
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "payer_account_id": payer_account_id,
            "amount": amount,
            "task_id": task_id,
            "created_at": created_at,
            "tx_id": tx_id,
            "event": self._build_bank_event(
                event_type="escrow.locked",
                summary=f"Locked {amount} credits in escrow for {task_id}",
                payload={"escrow_id": escrow_id, "amount": amount, "title": task_id},
                task_id=task_id,
                agent_id=payer_account_id,
            ),
        }
        response = await self._post_raw("/bank/escrow/lock", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        self._raise_status_error("/bank/escrow/lock", response)

    async def escrow_release(
        self,
        escrow_id: str,
        recipient_account_id: str,
        tx_id: str,
        resolved_at: str,
        amount: int,
        constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /bank/escrow/release."""
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_account_id,
            "tx_id": tx_id,
            "resolved_at": resolved_at,
            "event": self._build_bank_event(
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
        if constraints is not None:
            payload["constraints"] = constraints
        response = await self._post_raw("/bank/escrow/release", payload)
        if response.status_code == 200:
            return self._response_dict(response)
        self._raise_status_error("/bank/escrow/release", response)

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
        constraints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /bank/escrow/split."""
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "worker_account_id": worker_account_id,
            "poster_account_id": poster_account_id,
            "worker_tx_id": worker_tx_id,
            "poster_tx_id": poster_tx_id,
            "resolved_at": resolved_at,
            "worker_amount": worker_amount,
            "poster_amount": poster_amount,
            "event": self._build_bank_event(
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
        if constraints is not None:
            payload["constraints"] = constraints
        response = await self._post_raw("/bank/escrow/split", payload)
        if response.status_code == 200:
            return self._response_dict(response)
        self._raise_status_error("/bank/escrow/split", response)

    # ------------------------------------------------------------------
    # Reputation — /reputation/feedback
    # ------------------------------------------------------------------

    async def submit_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /reputation/feedback. `payload` is the full gateway request body
        (feedback_id, task_id, from_agent_id, to_agent_id, role, category, rating,
        comment, submitted_at, force_visible, event) — reputation owns the shape.
        """
        response = await self._post_raw("/reputation/feedback", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        self._raise_status_error("/reputation/feedback", response)

    async def get_feedback(self, feedback_id: str) -> dict[str, Any] | None:
        """GET /reputation/feedback/{feedback_id}. Returns None on 404."""
        return await self._get(
            f"/reputation/feedback/{feedback_id}",
            expected_status=200,
            not_found_returns_none=True,
        )

    async def list_feedback(
        self, *, task_id: str | None = None, agent_id: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /reputation/feedback?task_id=...|agent_id=..."""
        params: dict[str, str] = {}
        if task_id is not None:
            params["task_id"] = task_id
        if agent_id is not None:
            params["agent_id"] = agent_id
        query = "&".join(f"{k}={v}" for k, v in params.items())
        path = "/reputation/feedback" + (f"?{query}" if query else "")
        response = await self._get_raw(path)
        if response.status_code != 200:
            self._raise_status_error("/reputation/feedback", response)
        items = self._response_dict(response).get("feedback", [])
        return [f for f in items if isinstance(f, dict)] if isinstance(items, list) else []

    async def count_feedback(self) -> int:
        """GET /reputation/feedback/count."""
        response = await self._get_raw("/reputation/feedback/count")
        if response.status_code != 200:
            self._raise_status_error("/reputation/feedback/count", response)
        return int(self._response_dict(response).get("count", 0))

    # ------------------------------------------------------------------
    # Court — /court/claims, /court/rebuttals, /court/rulings
    # ------------------------------------------------------------------

    async def file_claim(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /court/claims."""
        response = await self._post_raw("/court/claims", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        self._raise_status_error("/court/claims", response)

    async def get_claim(self, claim_id: str) -> dict[str, Any] | None:
        """GET /court/claims/{claim_id}. Returns None on 404."""
        return await self._get(
            f"/court/claims/{claim_id}", expected_status=200, not_found_returns_none=True
        )

    async def list_claims(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """GET /court/claims?status=..."""
        path = "/court/claims" + (f"?status={status}" if status is not None else "")
        response = await self._get_raw(path)
        if response.status_code != 200:
            self._raise_status_error("/court/claims", response)
        items = self._response_dict(response).get("claims", [])
        return [c for c in items if isinstance(c, dict)] if isinstance(items, list) else []

    async def update_claim_status(
        self, claim_id: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """POST /court/claims/{claim_id}/status. Returns None on 404."""
        response = await self._post_raw(f"/court/claims/{claim_id}/status", payload)
        if response.status_code == 200:
            return self._response_dict(response)
        if response.status_code == 404:
            return None
        self._raise_status_error(f"/court/claims/{claim_id}/status", response)

    async def submit_rebuttal(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /court/rebuttals."""
        response = await self._post_raw("/court/rebuttals", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        self._raise_status_error("/court/rebuttals", response)

    async def get_rebuttal(self, claim_id: str) -> dict[str, Any] | None:
        """GET /court/claims/{claim_id}/rebuttal. Returns None on 404."""
        return await self._get(
            f"/court/claims/{claim_id}/rebuttal",
            expected_status=200,
            not_found_returns_none=True,
        )

    async def record_ruling(self, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /court/rulings."""
        response = await self._post_raw("/court/rulings", payload)
        if response.status_code in (200, 201):
            return self._response_dict(response)
        self._raise_status_error("/court/rulings", response)

    async def get_ruling(self, claim_id: str) -> dict[str, Any] | None:
        """GET /court/rulings/{claim_id}. Returns None on 404."""
        return await self._get(
            f"/court/rulings/{claim_id}", expected_status=200, not_found_returns_none=True
        )

    async def delete_ruling(self, claim_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """DELETE /court/rulings/{claim_id}. Returns None on 404."""
        response = await self._delete_raw(f"/court/rulings/{claim_id}", payload)
        if response.status_code == 200:
            return self._response_dict(response)
        if response.status_code == 404:
            return None
        self._raise_status_error(f"/court/rulings/{claim_id}", response)

    async def count_claims(self) -> int:
        """GET /court/claims/count."""
        response = await self._get_raw("/court/claims/count")
        if response.status_code != 200:
            self._raise_status_error("/court/claims/count", response)
        return int(self._response_dict(response).get("count", 0))

    async def count_active_claims(self) -> int:
        """GET /court/claims/count-active."""
        response = await self._get_raw("/court/claims/count-active")
        if response.status_code != 200:
            self._raise_status_error("/court/claims/count-active", response)
        return int(self._response_dict(response).get("count", 0))

    # ------------------------------------------------------------------
    # Board (read-only) — /board/tasks/{task_id}
    #
    # Court's DisputeDbClient needs a task's escrow_id; this is the one board
    # read route a non-task-board service actually uses.
    # ------------------------------------------------------------------

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """GET /board/tasks/{task_id}. Returns None on 404."""
        return await self._get(
            f"/board/tasks/{task_id}", expected_status=200, not_found_returns_none=True
        )

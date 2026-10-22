"""Explicit persisted external effects for the Task Board router fixtures."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from db_gateway_service.task_time import add_seconds_to_epoch_us, epoch_us_to_utc_text
from service_auth.financial_identity import operation_id_for

if TYPE_CHECKING:
    from collections.abc import Callable

    from service_auth.jws import VerifiedJws

    from tests.fakes.gateway_task_store import GatewayTaskStore


class RouterGatewayEffects:
    """Persist supplied intents; never invent a task, party or native command."""

    def __init__(
        self,
        store: GatewayTaskStore,
        clock_us: Callable[[], int],
        rebuttal_window_seconds: int,
        verify_token: Callable[[str], VerifiedJws],
    ) -> None:
        self.store = store
        self.clock_us = clock_us
        self.rebuttal_window_seconds = rebuttal_window_seconds
        self.verify_token = verify_token

    def event(
        self, source: str, event_type: str, task_id: str | None, agent_id: str | None
    ) -> dict[str, Any]:
        return {
            "event_source": source,
            "event_type": event_type,
            "timestamp": epoch_us_to_utc_text(self.clock_us()),
            "task_id": task_id,
            "agent_id": agent_id,
            "summary": event_type,
            "payload": "{}",
        }

    def register_actor(self, agent_id: str, public_key: str, opening_balance: int) -> None:
        timestamp = epoch_us_to_utc_text(self.clock_us())
        self.store.writer.register_agent(
            {
                "agent_id": agent_id,
                "name": agent_id,
                "public_key": public_key,
                "registered_at": timestamp,
                "event": self.event("identity", "agent.registered", None, agent_id),
            }
        )
        account: dict[str, Any] = {
            "account_id": agent_id,
            "balance": opening_balance,
            "created_at": timestamp,
            "event": self.event("bank", "account.created", None, agent_id),
        }
        if opening_balance:
            account["operation_id"] = operation_id_for("router-test-funding", (agent_id,))
            account["initial_credit"] = {
                "tx_id": f"tx-{uuid.uuid4()}",
                "amount": opening_balance,
                "reference": "initial_balance",
                "timestamp": timestamp,
            }
        self.store.writer.create_account(account)

    async def lock(self, escrow_token: str) -> dict[str, Any]:
        payload = self.verify_token(escrow_token).payload
        task_id = payload["task_id"]
        agent_id = payload["agent_id"]
        assert isinstance(task_id, str) and isinstance(agent_id, str)
        created_at = epoch_us_to_utc_text(self.clock_us())
        event = self.event("central-bank", "escrow.locked", task_id, agent_id)
        event["timestamp"] = created_at
        return self.store.writer.escrow_lock(
            {
                "operation_id": payload["operation_id"],
                "escrow_id": f"esc-{uuid.uuid4()}",
                "payer_account_id": payload["agent_id"],
                "amount": payload["amount"],
                "task_id": payload["task_id"],
                "created_at": created_at,
                "tx_id": f"tx-{uuid.uuid4()}",
                "event": event,
            }
        )

    async def release(
        self,
        *,
        escrow_id: str,
        recipient_account_id: str,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        escrow = self.store.writer.connection.execute(
            "SELECT task_id FROM bank_escrow WHERE escrow_id=?", (escrow_id,)
        ).fetchone()
        assert escrow is not None
        return self.store.writer.escrow_release(
            {
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "recipient_account_id": recipient_account_id,
                "source_reference": source_reference,
                "resolved_at": epoch_us_to_utc_text(self.clock_us()),
                "tx_id": f"tx-{uuid.uuid4()}",
                "event": self.event(
                    "central-bank", "escrow.released", str(escrow[0]), recipient_account_id
                ),
            }
        )

    async def split(
        self,
        *,
        escrow_id: str,
        worker_account_id: str,
        poster_account_id: str,
        worker_pct: int,
        operation_id: str,
        source_reference: str,
    ) -> dict[str, Any]:
        escrow = self.store.writer.connection.execute(
            "SELECT task_id FROM bank_escrow WHERE escrow_id=?", (escrow_id,)
        ).fetchone()
        assert escrow is not None
        return self.store.writer.escrow_split(
            {
                "operation_id": operation_id,
                "escrow_id": escrow_id,
                "worker_account_id": worker_account_id,
                "poster_account_id": poster_account_id,
                "worker_pct": worker_pct,
                "source_reference": source_reference,
                "resolved_at": epoch_us_to_utc_text(self.clock_us()),
                "worker_tx_id": f"tx-{uuid.uuid4()}",
                "poster_tx_id": f"tx-{uuid.uuid4()}",
                "event": self.event(
                    "central-bank", "escrow.split", str(escrow[0]), worker_account_id
                ),
            }
        )

    async def file_claim(
        self, *, task_id: str, claimant_id: str, respondent_id: str, claim: str, escrow_id: str
    ) -> dict[str, Any]:
        return await self.persist_claim(
            claim_id=f"clm-{uuid.uuid4()}",
            task_id=task_id,
            claimant_id=claimant_id,
            respondent_id=respondent_id,
            claim=claim,
            escrow_id=escrow_id,
        )

    async def persist_claim(
        self,
        *,
        claim_id: str,
        task_id: str,
        claimant_id: str,
        respondent_id: str,
        claim: str,
        escrow_id: str,
    ) -> dict[str, Any]:
        """Persist an explicit claim identity for acknowledgment and binding scenarios."""
        task = self.store.get_task(task_id)
        assert task is not None and task["escrow_id"] == escrow_id
        now_us = self.clock_us()
        deadline = epoch_us_to_utc_text(
            add_seconds_to_epoch_us(now_us, self.rebuttal_window_seconds)
        )
        self.store.writer.file_claim(
            {
                "claim_id": claim_id,
                "task_id": task_id,
                "claimant_id": claimant_id,
                "respondent_id": respondent_id,
                "reason": claim,
                "status": "rebuttal_pending",
                "rebuttal_deadline": deadline,
                "filed_at": epoch_us_to_utc_text(now_us),
                "event": self.event("court", "claim.filed", task_id, claimant_id),
            }
        )
        return {"dispute_id": claim_id, "status": "rebuttal_pending", "rebuttal_deadline": deadline}

    async def submit_rebuttal(self, dispute_id: str, rebuttal: str) -> dict[str, Any]:
        claim = self.store.writer.connection.execute(
            "SELECT task_id,respondent_id FROM court_claims WHERE claim_id=?", (dispute_id,)
        ).fetchone()
        assert claim is not None
        self.store.writer.submit_rebuttal(
            {
                "rebuttal_id": f"reb-{uuid.uuid4()}",
                "claim_id": dispute_id,
                "agent_id": claim[1],
                "content": rebuttal,
                "submitted_at": epoch_us_to_utc_text(self.clock_us()),
                "event": self.event("court", "rebuttal.submitted", str(claim[0]), str(claim[1])),
            },
            None,
        )
        return {"dispute_id": dispute_id, "status": "rebuttal_pending", "rebuttal": rebuttal}

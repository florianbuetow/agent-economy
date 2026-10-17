"""POST /court/claims/{claim_id}/status must classify a foreign-key
violation on its event insert the same way every sibling CourtWriter method
does — not let it escape as an uncaught 500 (SEC-05; T-072 sweep finding).

`CourtWriter.update_claim_status` was the one method in this class missing
the `except sqlite3.IntegrityError` classification block its four siblings
(`file_claim`, `submit_rebuttal`, `record_ruling`, and one more) all have —
a raw FK violation on the event's `agent_id` (which FKs to
`identity_agents.agent_id`) propagated as an unhandled `sqlite3.IntegrityError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _register_agent(client: TestClient, agent_id: str | None = None, name: str = "Test") -> str:
    aid = agent_id or f"a-{uuid4()}"
    client.post(
        "/identity/agents",
        json={
            "agent_id": aid,
            "name": name,
            "public_key": f"ed25519:{uuid4()}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        },
    )
    return aid


def _setup_funded_account(client: TestClient, name: str = "Test", balance: int = 500) -> str:
    aid = _register_agent(client, name=name)
    client.post(
        "/bank/accounts",
        json={
            "operation_id": f"op-{uuid4()}",
            "account_id": aid,
            "balance": balance,
            "created_at": "2026-02-28T10:00:00Z",
            "initial_credit": {
                "tx_id": f"tx-{uuid4()}",
                "amount": balance,
                "reference": "initial_balance",
                "timestamp": "2026-02-28T10:00:00Z",
            },
            "event": make_event(source="bank", event_type="account.created"),
        },
    )
    return aid


def _create_task(client: TestClient, poster_id: str) -> str:
    tid = f"t-{uuid4()}"
    eid = f"esc-{uuid4()}"
    client.post(
        "/bank/escrow/lock",
        json={
            "operation_id": f"op-{uuid4()}",
            "escrow_id": eid,
            "payer_account_id": poster_id,
            "amount": 100,
            "task_id": tid,
            "created_at": "2026-02-28T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=tid),
        },
    )
    client.post(
        "/board/tasks",
        json={
            "task_id": tid,
            "poster_id": poster_id,
            "title": "Test Task",
            "spec": "Build a login page",
            "reward": 100,
            "status": "open",
            "bidding_deadline_seconds": 86400,
            "deadline_seconds": 172800,
            "review_deadline_seconds": 43200,
            "bidding_deadline": "2026-03-01T10:00:00Z",
            "escrow_id": eid,
            "created_at": "2026-02-28T10:15:00Z",
            "event": make_event(source="board", event_type="task.created", task_id=tid),
        },
    )
    return tid


def _file_claim(client: TestClient, task_id: str, claimant_id: str, respondent_id: str) -> str:
    cid = f"clm-{uuid4()}"
    client.post(
        "/court/claims",
        json={
            "claim_id": cid,
            "task_id": task_id,
            "claimant_id": claimant_id,
            "respondent_id": respondent_id,
            "reason": "The login page does not validate email format",
            "status": "filed",
            "filed_at": "2026-02-28T16:00:00Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
        },
    )
    return cid


@pytest.mark.unit
class TestUpdateClaimStatusForeignKeyViolation:
    def test_status_update_with_unknown_event_agent_returns_409_not_500(
        self, app_with_writer: TestClient
    ) -> None:
        alice = _setup_funded_account(app_with_writer, name="Alice")
        bob = _register_agent(app_with_writer, name="Bob")
        task_id = _create_task(app_with_writer, alice)
        claim_id = _file_claim(app_with_writer, task_id, alice, bob)

        response = app_with_writer.post(
            f"/court/claims/{claim_id}/status",
            json={
                "status": "rebuttal",
                "event": make_event(
                    source="court",
                    event_type="claim.status_updated",
                    task_id=task_id,
                    agent_id="a-does-not-exist",
                ),
            },
        )

        assert response.status_code == 409
        assert response.json()["error"] == "foreign_key_violation"

    def test_status_update_with_known_agent_still_succeeds(
        self, app_with_writer: TestClient
    ) -> None:
        alice = _setup_funded_account(app_with_writer, name="Alice")
        bob = _register_agent(app_with_writer, name="Bob")
        task_id = _create_task(app_with_writer, alice)
        claim_id = _file_claim(app_with_writer, task_id, alice, bob)

        response = app_with_writer.post(
            f"/court/claims/{claim_id}/status",
            json={
                "status": "rebuttal",
                "event": make_event(
                    source="court",
                    event_type="claim.status_updated",
                    task_id=task_id,
                    agent_id=bob,
                ),
            },
        )

        assert response.status_code == 200
        assert response.json()["status"] == "rebuttal"

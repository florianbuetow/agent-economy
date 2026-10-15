"""T-072 coverage-vs-spec-ID sweep: gap-fill tests for court read endpoints.

Covers READ-27 (GET /court/claims filters by claimant_id — status filter was
already covered), READ-29/READ-30 (GET /court/claims/{claim_id}/rebuttal
found/not found), and READ-31/READ-32 (GET /court/rulings/{claim_id}
found/not found). All four endpoints already exist and are correctly wired in
routers/court.py + services/db_reader.py; only the tests were missing
(verified via a full literal-ID grep across services/db-gateway/tests/ before
writing).

New file — no existing test file was modified.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


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


def _setup_funded_account(
    client: TestClient, agent_id: str | None = None, balance: int = 500, name: str = "Test"
) -> str:
    aid = _register_agent(client, agent_id, name=name)
    data: dict[str, Any] = {
        "account_id": aid,
        "balance": balance,
        "created_at": "2026-02-28T10:00:00Z",
        "event": make_event(source="bank", event_type="account.created"),
    }
    if balance > 0:
        data["initial_credit"] = {
            "tx_id": f"tx-{uuid4()}",
            "amount": balance,
            "reference": "initial_balance",
            "timestamp": "2026-02-28T10:00:00Z",
        }
    client.post("/bank/accounts", json=data)
    return aid


def _lock_escrow(
    client: TestClient, payer_id: str, amount: int = 100, task_id: str | None = None
) -> tuple[str, str]:
    tid = task_id or f"t-{uuid4()}"
    eid = f"esc-{uuid4()}"
    client.post(
        "/bank/escrow/lock",
        json={
            "escrow_id": eid,
            "payer_account_id": payer_id,
            "amount": amount,
            "task_id": tid,
            "created_at": "2026-02-28T10:10:00Z",
            "tx_id": f"tx-{uuid4()}",
            "event": make_event(source="bank", event_type="escrow.locked", task_id=tid),
        },
    )
    return eid, tid


def _create_task(client: TestClient, poster_id: str | None = None) -> tuple[str, str, str]:
    pid = poster_id or _setup_funded_account(client)
    eid, tid = _lock_escrow(client, pid)
    client.post(
        "/board/tasks",
        json={
            "task_id": tid,
            "poster_id": pid,
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
    return tid, pid, eid


def _setup_court_prerequisites(client: TestClient) -> tuple[str, str, str]:
    """Create alice, bob, and a task. Returns (alice, bob, task_id)."""
    alice = _setup_funded_account(client, name="Alice")
    bob = _register_agent(client, name="Bob")
    tid, _pid, _eid = _create_task(client, poster_id=alice)
    return alice, bob, tid


def _file_claim(
    client: TestClient,
    task_id: str,
    claimant_id: str,
    respondent_id: str,
    claim_id: str | None = None,
) -> str:
    cid = claim_id or f"clm-{uuid4()}"
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


def _submit_rebuttal(client: TestClient, claim_id: str, agent_id: str, task_id: str) -> str:
    rebuttal_id = f"reb-{uuid4()}"
    client.post(
        "/court/rebuttals",
        json={
            "rebuttal_id": rebuttal_id,
            "claim_id": claim_id,
            "agent_id": agent_id,
            "content": "The specification did not mention email validation",
            "submitted_at": "2026-02-28T17:00:00Z",
            "event": make_event(source="court", event_type="rebuttal.submitted", task_id=task_id),
        },
    )
    return rebuttal_id


def _record_ruling(client: TestClient, claim_id: str, task_id: str) -> str:
    ruling_id = f"rul-{uuid4()}"
    client.post(
        "/court/rulings",
        json={
            "ruling_id": ruling_id,
            "claim_id": claim_id,
            "task_id": task_id,
            "worker_pct": 70,
            "summary": "The specification was ambiguous about email validation",
            "judge_votes": '[{"judge_id": "judge-0", "worker_pct": 70, '
            '"reasoning": "Spec did not mention email format"}]',
            "ruled_at": "2026-02-28T18:00:00Z",
            "event": make_event(
                source="court", event_type="ruling.delivered", task_id=task_id, agent_id=None
            ),
        },
    )
    return ruling_id


@pytest.mark.unit
class TestReadClaimsFilterByClaimantId:
    """READ-27: GET /court/claims filters by claimant_id (status filter is
    already covered by test_court_reads.py)."""

    def test_filter_by_claimant_id(self, app_with_writer: TestClient) -> None:
        alice, bob, tid1 = _setup_court_prerequisites(app_with_writer)
        carol = _register_agent(app_with_writer, name="Carol")
        tid2, _pid2, _eid2 = _create_task(app_with_writer, poster_id=alice)

        claim_from_alice = _file_claim(app_with_writer, tid1, alice, bob)
        _file_claim(app_with_writer, tid2, carol, bob)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/claims?claimant_id={alice}")

        assert response.status_code == 200
        claims = response.json()["claims"]
        assert len(claims) == 1
        assert claims[0]["claim_id"] == claim_from_alice
        assert claims[0]["claimant_id"] == alice


@pytest.mark.unit
class TestReadRebuttalByClaimId:
    """READ-29/READ-30: GET /court/claims/{claim_id}/rebuttal."""

    def test_get_rebuttal_returns_the_record(self, app_with_writer: TestClient) -> None:
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        rebuttal_id = _submit_rebuttal(app_with_writer, claim_id, bob, tid)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/claims/{claim_id}/rebuttal")

        assert response.status_code == 200
        data = response.json()
        assert data["rebuttal_id"] == rebuttal_id
        assert data["claim_id"] == claim_id
        assert data["agent_id"] == bob

    def test_get_rebuttal_not_found(self, app_with_writer: TestClient) -> None:
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/claims/{claim_id}/rebuttal")

        assert response.status_code == 404
        assert response.json()["error"] == "rebuttal_not_found"


@pytest.mark.unit
class TestReadRulingByClaimId:
    """READ-31/READ-32: GET /court/rulings/{claim_id}."""

    def test_get_ruling_returns_the_record(self, app_with_writer: TestClient) -> None:
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        ruling_id = _record_ruling(app_with_writer, claim_id, tid)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/rulings/{claim_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["ruling_id"] == ruling_id
        assert data["claim_id"] == claim_id
        assert data["worker_pct"] == 70

    def test_get_ruling_not_found(self, app_with_writer: TestClient) -> None:
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        claim_id = _file_claim(app_with_writer, tid, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/rulings/{claim_id}")

        assert response.status_code == 404
        assert response.json()["error"] == "ruling_not_found"

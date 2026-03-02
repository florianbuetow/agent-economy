"""Court read endpoint tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _register_agent(
    client: TestClient,
    agent_id: str | None = None,
    name: str = "Test",
) -> str:
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
    client: TestClient,
    agent_id: str | None = None,
    balance: int = 500,
    name: str = "Test",
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
    client: TestClient,
    payer_id: str,
    amount: int = 100,
    task_id: str | None = None,
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


def _create_task(
    client: TestClient,
    poster_id: str | None = None,
) -> tuple[str, str, str]:
    """Create a full task. Returns (task_id, poster_id, escrow_id)."""
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


def _setup_court_prerequisites(
    client: TestClient,
) -> tuple[str, str, str]:
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
    status: str = "filed",
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
            "status": status,
            "filed_at": "2026-02-28T16:00:00Z",
            "event": make_event(source="court", event_type="claim.filed", task_id=task_id),
        },
    )
    return cid


# ===========================================================================
# Court Read Endpoint Tests
# ===========================================================================


@pytest.mark.unit
class TestCourtReads:
    """Tests for court read endpoints."""

    def test_get_claim_by_id(self, app_with_writer: TestClient) -> None:
        """GET /court/claims/{id} returns the claim record."""
        alice, bob, tid = _setup_court_prerequisites(app_with_writer)
        cid = _file_claim(app_with_writer, tid, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get(f"/court/claims/{cid}")

        assert response.status_code == 200
        data = response.json()
        assert data["claim_id"] == cid
        assert data["task_id"] == tid
        assert data["claimant_id"] == alice
        assert data["respondent_id"] == bob
        assert data["status"] == "filed"
        assert "filed_at" in data

    def test_get_claim_not_found(self, app_with_writer: TestClient) -> None:
        """GET /court/claims/{id} returns 404 when missing."""
        _wire_db_reader()

        response = app_with_writer.get("/court/claims/clm-nonexistent")

        assert response.status_code == 404
        assert response.json()["error"] == "claim_not_found"

    def test_list_claims_empty(self, app_with_writer: TestClient) -> None:
        """GET /court/claims returns empty list when no claims exist."""
        _wire_db_reader()

        response = app_with_writer.get("/court/claims")

        assert response.status_code == 200
        assert response.json() == {"claims": []}

    def test_list_claims_returns_all(self, app_with_writer: TestClient) -> None:
        """GET /court/claims returns all claims."""
        alice, bob, tid1 = _setup_court_prerequisites(app_with_writer)
        tid2, _pid2, _eid2 = _create_task(app_with_writer, poster_id=alice)
        _file_claim(app_with_writer, tid1, alice, bob)
        _file_claim(app_with_writer, tid2, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get("/court/claims")

        assert response.status_code == 200
        claims = response.json()["claims"]
        assert len(claims) == 2

    def test_list_claims_filter_by_status(self, app_with_writer: TestClient) -> None:
        """GET /court/claims?status=filed returns only matching claims."""
        alice, bob, tid1 = _setup_court_prerequisites(app_with_writer)
        tid2, _pid2, _eid2 = _create_task(app_with_writer, poster_id=alice)
        _file_claim(app_with_writer, tid1, alice, bob, status="filed")
        cid2 = _file_claim(app_with_writer, tid2, alice, bob, status="filed")

        # Update one claim to "ruled"
        app_with_writer.post(
            f"/court/claims/{cid2}/status",
            json={"status": "ruled"},
        )
        _wire_db_reader()

        response = app_with_writer.get("/court/claims?status=filed")

        assert response.status_code == 200
        claims = response.json()["claims"]
        assert len(claims) == 1
        assert claims[0]["status"] == "filed"

    def test_count_claims_zero(self, app_with_writer: TestClient) -> None:
        """GET /court/claims/count returns zero when empty."""
        _wire_db_reader()

        response = app_with_writer.get("/court/claims/count")

        assert response.status_code == 200
        assert response.json() == {"count": 0}

    def test_count_claims_after_filing(self, app_with_writer: TestClient) -> None:
        """GET /court/claims/count reflects filed claims."""
        alice, bob, tid1 = _setup_court_prerequisites(app_with_writer)
        tid2, _pid2, _eid2 = _create_task(app_with_writer, poster_id=alice)
        tid3, _pid3, _eid3 = _create_task(app_with_writer, poster_id=alice)
        _file_claim(app_with_writer, tid1, alice, bob)
        _file_claim(app_with_writer, tid2, alice, bob)
        _file_claim(app_with_writer, tid3, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get("/court/claims/count")

        assert response.status_code == 200
        assert response.json() == {"count": 3}

    def test_count_active_claims_all_active(self, app_with_writer: TestClient) -> None:
        """GET /court/claims/count-active counts all non-ruled claims."""
        alice, bob, tid1 = _setup_court_prerequisites(app_with_writer)
        tid2, _pid2, _eid2 = _create_task(app_with_writer, poster_id=alice)
        _file_claim(app_with_writer, tid1, alice, bob)
        _file_claim(app_with_writer, tid2, alice, bob)
        _wire_db_reader()

        response = app_with_writer.get("/court/claims/count-active")

        assert response.status_code == 200
        assert response.json() == {"count": 2}

    def test_count_active_claims_some_ruled(self, app_with_writer: TestClient) -> None:
        """GET /court/claims/count-active excludes ruled claims."""
        alice, bob, tid1 = _setup_court_prerequisites(app_with_writer)
        tid2, _pid2, _eid2 = _create_task(app_with_writer, poster_id=alice)
        _file_claim(app_with_writer, tid1, alice, bob)
        cid2 = _file_claim(app_with_writer, tid2, alice, bob)

        # Rule one claim
        app_with_writer.post(
            f"/court/claims/{cid2}/status",
            json={"status": "ruled"},
        )
        _wire_db_reader()

        response = app_with_writer.get("/court/claims/count-active")

        assert response.status_code == 200
        assert response.json() == {"count": 1}

    def test_count_active_claims_zero(self, app_with_writer: TestClient) -> None:
        """GET /court/claims/count-active returns zero when empty."""
        _wire_db_reader()

        response = app_with_writer.get("/court/claims/count-active")

        assert response.status_code == 200
        assert response.json() == {"count": 0}

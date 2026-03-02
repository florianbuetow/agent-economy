"""Bank read endpoint tests."""

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


def _register_agent(client: TestClient, agent_id: str | None = None, name: str = "Test") -> str:
    """Register an agent and return agent_id."""
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


def _create_account(client: TestClient, agent_id: str, balance: int = 0) -> None:
    """Create account, optionally with initial credit."""
    data: dict[str, Any] = {
        "account_id": agent_id,
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


def _setup_funded_account(
    client: TestClient,
    agent_id: str | None = None,
    balance: int = 500,
) -> str:
    """Register agent + create account + fund."""
    aid = _register_agent(client, agent_id)
    _create_account(client, aid, balance)
    return aid


def _lock_escrow(
    client: TestClient,
    payer_id: str,
    amount: int = 100,
    task_id: str | None = None,
) -> tuple[str, str]:
    """Lock escrow and return (escrow_id, task_id)."""
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


# ===========================================================================
# Bank Read Endpoint Tests
# ===========================================================================


@pytest.mark.unit
class TestBankReads:
    """Tests for bank read endpoints."""

    def test_get_account_by_id(self, app_with_writer: TestClient) -> None:
        """GET /bank/accounts/{id} returns the account record."""
        aid = _setup_funded_account(app_with_writer, balance=500)
        _wire_db_reader()

        response = app_with_writer.get(f"/bank/accounts/{aid}")

        assert response.status_code == 200
        data = response.json()
        assert data["account_id"] == aid
        assert data["balance"] == 500
        assert "created_at" in data

    def test_get_account_not_found(self, app_with_writer: TestClient) -> None:
        """GET /bank/accounts/{id} returns 404 when missing."""
        _wire_db_reader()

        response = app_with_writer.get("/bank/accounts/a-nonexistent")

        assert response.status_code == 404
        assert response.json()["error"] == "account_not_found"

    def test_get_transactions_with_credit(self, app_with_writer: TestClient) -> None:
        """GET /bank/accounts/{id}/transactions returns transaction list."""
        aid = _setup_funded_account(app_with_writer, balance=500)
        _wire_db_reader()

        response = app_with_writer.get(f"/bank/accounts/{aid}/transactions")

        assert response.status_code == 200
        txns = response.json()["transactions"]
        assert isinstance(txns, list)
        assert len(txns) >= 1
        assert txns[0]["amount"] == 500
        assert "type" in txns[0]

    def test_get_transactions_empty(self, app_with_writer: TestClient) -> None:
        """GET /bank/accounts/{id}/transactions returns empty list for zero-balance account."""
        aid = _setup_funded_account(app_with_writer, balance=0)
        _wire_db_reader()

        response = app_with_writer.get(f"/bank/accounts/{aid}/transactions")

        assert response.status_code == 200
        assert response.json()["transactions"] == []

    def test_count_accounts_zero(self, app_with_writer: TestClient) -> None:
        """GET /bank/accounts/count returns zero when empty."""
        _wire_db_reader()

        response = app_with_writer.get("/bank/accounts/count")

        assert response.status_code == 200
        assert response.json() == {"count": 0}

    def test_count_accounts_after_creation(self, app_with_writer: TestClient) -> None:
        """GET /bank/accounts/count reflects created accounts."""
        _setup_funded_account(app_with_writer)
        _setup_funded_account(app_with_writer)
        _wire_db_reader()

        response = app_with_writer.get("/bank/accounts/count")

        assert response.status_code == 200
        assert response.json() == {"count": 2}

    def test_total_escrowed_zero(self, app_with_writer: TestClient) -> None:
        """GET /bank/escrow/total-locked returns zero when no escrow."""
        _wire_db_reader()

        response = app_with_writer.get("/bank/escrow/total-locked")

        assert response.status_code == 200
        assert response.json() == {"total": 0}

    def test_total_escrowed_after_locks(self, app_with_writer: TestClient) -> None:
        """GET /bank/escrow/total-locked sums locked escrow amounts."""
        aid = _setup_funded_account(app_with_writer, balance=1000)
        _lock_escrow(app_with_writer, aid, amount=100)
        _lock_escrow(app_with_writer, aid, amount=200)
        _wire_db_reader()

        response = app_with_writer.get("/bank/escrow/total-locked")

        assert response.status_code == 200
        assert response.json() == {"total": 300}

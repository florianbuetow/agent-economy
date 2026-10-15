"""T-072 coverage-vs-spec-ID sweep: gap-fill tests for bank read endpoints.

Covers READ-08 (transaction history ordering, strengthened to exercise 2+
rows), READ-10, READ-11 (GET /bank/escrow/{escrow_id} found/not found), and
READ-12 (a literal-string escrow_id that collides with a POST-only route name
is rejected as `escrow_not_found`, not misrouted). Confirmed by reading
services/db-gateway/src/db_gateway_service/routers/bank.py and
services/db_gateway_service/services/db_reader.py before writing: `GET
/bank/escrow/{escrow_id}` and the total-locked/lock/release/split names are
registered as distinct routes, so `GET /bank/escrow/lock` simply falls
through to the `{escrow_id}` matcher with escrow_id="lock".

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


def _create_account(client: TestClient, agent_id: str, balance: int = 0) -> None:
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
    aid = _register_agent(client, agent_id)
    _create_account(client, aid, balance)
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


@pytest.mark.unit
class TestReadOrderedTransactionHistory:
    """READ-08: GET /bank/accounts/{id}/transactions lists history in order."""

    def test_transactions_are_ordered_by_timestamp(self, app_with_writer: TestClient) -> None:
        aid = _setup_funded_account(app_with_writer, balance=100)
        credit_response = app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": f"tx-{uuid4()}",
                "account_id": aid,
                "amount": 50,
                "reference": "second_credit",
                "timestamp": "2026-02-28T11:00:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert credit_response.status_code == 200
        _wire_db_reader()

        response = app_with_writer.get(f"/bank/accounts/{aid}/transactions")

        assert response.status_code == 200
        txns = response.json()["transactions"]
        assert len(txns) == 2
        timestamps = [t["timestamp"] for t in txns]
        assert timestamps == sorted(timestamps)
        assert txns[0]["amount"] == 100
        assert txns[1]["amount"] == 50


@pytest.mark.unit
class TestReadEscrowSingleRecord:
    """READ-10/READ-11: GET /bank/escrow/{escrow_id}."""

    def test_get_escrow_by_id_returns_the_record(self, app_with_writer: TestClient) -> None:
        payer_id = _setup_funded_account(app_with_writer, balance=500)
        escrow_id, task_id = _lock_escrow(app_with_writer, payer_id, amount=100)
        _wire_db_reader()

        response = app_with_writer.get(f"/bank/escrow/{escrow_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["escrow_id"] == escrow_id
        assert data["amount"] == 100
        assert data["task_id"] == task_id
        assert data["status"] == "locked"

    def test_get_escrow_not_found(self, app_with_writer: TestClient) -> None:
        _wire_db_reader()

        response = app_with_writer.get("/bank/escrow/esc-does-not-exist")

        assert response.status_code == 404
        assert response.json()["error"] == "escrow_not_found"


@pytest.mark.unit
class TestReadEscrowLiteralIdNotMisrouted:
    """READ-12: GET /bank/escrow/lock|release|split as a literal ID is rejected,
    not misrouted.

    These path segments are also registered as POST-only routes
    (/bank/escrow/lock, /release, /split). A GET on the exact same path must
    be blocked as a wrong-method 405 by the route itself — it must NOT fall
    through to the GET /bank/escrow/{escrow_id} matcher and report a false
    `404 escrow_not_found` for an escrow literally named "lock"/etc.
    """

    @pytest.mark.parametrize("literal_id", ["lock", "release", "split"])
    def test_literal_action_name_as_escrow_id_returns_method_not_allowed(
        self, app_with_writer: TestClient, literal_id: str
    ) -> None:
        _wire_db_reader()

        response = app_with_writer.get(f"/bank/escrow/{literal_id}")

        assert response.status_code == 405
        assert response.json()["error"] == "method_not_allowed"

"""T-072 coverage-sweep gap fixes: GET /accounts/{id} and GET /accounts/{id}/transactions
scenarios from central-bank-service-auth-tests.md and central-bank-service-tests.md that
had no existing test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _setup_identity_mock(state: Any, agent_id: str = "a-test-agent") -> None:
    """Configure the mock identity client so account creation succeeds."""
    state.identity_client.get_agent = AsyncMock(
        return_value={"agent_id": agent_id, "name": "Test Agent"}
    )


async def _create_account(client: Any, private_key: Any, account_id: str, balance: int) -> None:
    token = make_jws_token(
        private_key,
        PLATFORM_AGENT_ID,
        {"action": "create_account", "agent_id": account_id, "initial_balance": balance},
    )
    resp = await client.post("/accounts", json={"token": token})
    assert resp.status_code == 201, resp.text


@pytest.mark.unit
class TestBearerAuthorizationHeaderShape:
    """BEARER-04: Authorization header without the "Bearer " prefix."""

    async def test_header_without_bearer_prefix_returns_invalid_jws(self, client):
        response = await client.get(
            "/accounts/a-alice",
            headers={"Authorization": "Token eyJ..."},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
class TestBalanceWrongActionAndMismatch:
    """BEARER-09/BAL-05 and BEARER-10/BAL-06: wrong action / payload-URL mismatch
    on GET /accounts/{account_id}. Both spec files describe the identical scenario
    for this endpoint, so one test satisfies both IDs."""

    async def test_wrong_action_for_correct_owner_returns_invalid_payload(
        self, client, agent_keypair
    ):
        """BEARER-09 / BAL-05: correct owner but wrong `action` returns invalid_payload."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key, "a-alice", {"action": "escrow_lock", "account_id": "a-alice"}
        )
        response = await client.get(
            "/accounts/a-alice",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_payload_account_id_mismatch_returns_payload_mismatch(
        self, client, agent_keypair
    ):
        """BEARER-10 / BAL-06: payload account_id different from the URL account_id
        returns payload_mismatch."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-alice",
            {"action": "get_balance", "account_id": "a-different-uuid"},
            resource="GET /accounts/a-alice",
        )
        response = await client.get(
            "/accounts/a-alice",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "payload_mismatch"


@pytest.mark.unit
class TestBalanceQueryScenarios:
    """BAL-02, BAL-03: balance accuracy and not-found."""

    async def test_balance_reflects_credits_and_escrow_locks(
        self, client, platform_keypair, agent_keypair
    ):
        """BAL-02: balance reflects credits and escrow locks accurately (100 + 50 - 30 = 120)."""
        state = get_app_state()
        _setup_identity_mock(state, agent_id="a-alice")
        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair

        await _create_account(client, platform_key, "a-alice", 100)

        credit_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-alice", "amount": 50, "reference": "bonus"},
        )
        credit_resp = await client.post("/accounts/a-alice/credit", json={"token": credit_token})
        assert credit_resp.status_code == 200

        lock_token = make_jws_token(
            agent_key,
            "a-alice",
            {"action": "escrow_lock", "agent_id": "a-alice", "amount": 30, "task_id": "T-001"},
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        assert lock_resp.status_code == 201

        balance_token = make_jws_token(
            agent_key, "a-alice", {"action": "get_balance", "account_id": "a-alice"}
        )
        response = await client.get(
            "/accounts/a-alice",
            headers={"Authorization": f"Bearer {balance_token}"},
        )
        assert response.status_code == 200
        assert response.json()["balance"] == 120

    async def test_account_not_found(self, client, agent_keypair):
        """BAL-03: querying a nonexistent account (signed by that same nonexistent
        id, so the ownership check passes) returns 404 account_not_found."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-nonexistent-uuid",
            {"action": "get_balance", "account_id": "a-nonexistent-uuid"},
        )
        response = await client.get(
            "/accounts/a-nonexistent-uuid",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
        assert response.json()["error"] == "account_not_found"


@pytest.mark.unit
class TestTransactionHistoryScenarios:
    """TX-01 through TX-07: transaction history endpoint behavior."""

    async def test_ordered_by_timestamp_then_tx_id(self, client, platform_keypair, agent_keypair):
        """TX-01: history is ordered ASC, first entry is the initial_balance credit."""
        state = get_app_state()
        _setup_identity_mock(state, agent_id="a-alice")
        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair

        await _create_account(client, platform_key, "a-alice", 100)
        for ref in ("ref_a", "ref_b"):
            credit_token = make_jws_token(
                platform_key,
                PLATFORM_AGENT_ID,
                {"action": "credit", "account_id": "a-alice", "amount": 10, "reference": ref},
            )
            resp = await client.post("/accounts/a-alice/credit", json={"token": credit_token})
            assert resp.status_code == 200

        tx_token = make_jws_token(
            agent_key, "a-alice", {"action": "get_transactions", "account_id": "a-alice"}
        )
        response = await client.get(
            "/accounts/a-alice/transactions",
            headers={"Authorization": f"Bearer {tx_token}"},
        )
        assert response.status_code == 200
        transactions = response.json()["transactions"]
        assert len(transactions) == 3
        assert transactions[0]["reference"] == "initial_balance"
        timestamps = [tx["timestamp"] for tx in transactions]
        assert timestamps == sorted(timestamps)

    async def test_empty_list_for_zero_balance_account(
        self, client, platform_keypair, agent_keypair
    ):
        """TX-02: a zero-initial-balance account has an empty transaction list."""
        state = get_app_state()
        _setup_identity_mock(state, agent_id="a-alice")
        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair

        await _create_account(client, platform_key, "a-alice", 0)

        tx_token = make_jws_token(
            agent_key, "a-alice", {"action": "get_transactions", "account_id": "a-alice"}
        )
        response = await client.get(
            "/accounts/a-alice/transactions",
            headers={"Authorization": f"Bearer {tx_token}"},
        )
        assert response.status_code == 200
        assert response.json()["transactions"] == []

    async def test_includes_credit_escrow_lock_and_escrow_release_types(
        self, client, platform_keypair, agent_keypair
    ):
        """TX-03: history includes credit, escrow_lock, and escrow_release types."""
        state = get_app_state()
        _setup_identity_mock(state, agent_id="a-alice")
        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair

        await _create_account(client, platform_key, "a-alice", 100)

        lock_token = make_jws_token(
            agent_key,
            "a-alice",
            {"action": "escrow_lock", "agent_id": "a-alice", "amount": 30, "task_id": "T-100"},
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        escrow_id = lock_resp.json()["escrow_id"]

        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": "a-alice",
            },
        )
        release_resp = await client.post(
            f"/escrow/{escrow_id}/release", json={"token": release_token}
        )
        assert release_resp.status_code == 200

        tx_token = make_jws_token(
            agent_key, "a-alice", {"action": "get_transactions", "account_id": "a-alice"}
        )
        response = await client.get(
            "/accounts/a-alice/transactions",
            headers={"Authorization": f"Bearer {tx_token}"},
        )
        types = {tx["type"] for tx in response.json()["transactions"]}
        assert types == {"credit", "escrow_lock", "escrow_release"}

    async def test_each_transaction_has_all_required_fields(
        self, client, platform_keypair, agent_keypair
    ):
        """TX-04: every transaction object has exactly the required fields."""
        state = get_app_state()
        _setup_identity_mock(state, agent_id="a-alice")
        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair

        await _create_account(client, platform_key, "a-alice", 50)

        tx_token = make_jws_token(
            agent_key, "a-alice", {"action": "get_transactions", "account_id": "a-alice"}
        )
        response = await client.get(
            "/accounts/a-alice/transactions",
            headers={"Authorization": f"Bearer {tx_token}"},
        )
        transactions = response.json()["transactions"]
        assert len(transactions) == 1
        expected_fields = {"tx_id", "type", "amount", "balance_after", "reference", "timestamp"}
        assert set(transactions[0]) == expected_fields
        assert transactions[0]["tx_id"].startswith("tx-")
        assert transactions[0]["amount"] > 0
        assert transactions[0]["balance_after"] >= 0

    async def test_account_not_found(self, client, agent_keypair):
        """TX-05: transactions for a nonexistent account return 404 account_not_found."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-nonexistent-uuid",
            {"action": "get_transactions", "account_id": "a-nonexistent-uuid"},
        )
        response = await client.get(
            "/accounts/a-nonexistent-uuid/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
        assert response.json()["error"] == "account_not_found"

    async def test_wrong_agent_accessing_anothers_transactions(
        self, client, platform_keypair, agent_keypair
    ):
        """TX-06 / BEARER-08: a different agent cannot view another agent's
        transaction history."""
        state = get_app_state()
        _setup_identity_mock(state, agent_id="a-alice")
        platform_key, _ = platform_keypair
        bob_key, _ = agent_keypair

        await _create_account(client, platform_key, "a-alice", 50)

        tx_token = make_jws_token(
            bob_key, "a-bob", {"action": "get_transactions", "account_id": "a-alice"}
        )
        response = await client.get(
            "/accounts/a-alice/transactions",
            headers={"Authorization": f"Bearer {tx_token}"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    async def test_wrong_action_returns_invalid_payload(self, client, agent_keypair):
        """TX-07: wrong `action` (get_balance instead of get_transactions) returns
        invalid_payload."""
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key, "a-alice", {"action": "get_balance", "account_id": "a-alice"}
        )
        response = await client.get(
            "/accounts/a-alice/transactions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

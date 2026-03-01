"""Account endpoint tests."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _setup_identity_mock_for_platform(
    app_state: Any,
    agent_exists: bool = True,
    agent_id: str = "a-test-agent",
) -> None:
    """Configure the mock identity client for platform operations."""

    async def mock_verify_jws(token: str) -> dict[str, Any]:
        # Decode the token to extract payload (trust it for testing)
        parts = token.split(".")
        header_b64 = parts[0]
        padding = 4 - len(header_b64) % 4
        if padding != 4:
            header_b64 += "=" * padding
        header = json.loads(base64.urlsafe_b64decode(header_b64))

        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        return {"valid": True, "agent_id": header["kid"], "payload": payload}

    app_state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)

    if agent_exists:
        app_state.identity_client.get_agent = AsyncMock(
            return_value={"agent_id": agent_id, "name": "Test Agent"}
        )
    else:
        app_state.identity_client.get_agent = AsyncMock(return_value=None)


@pytest.mark.unit
class TestCreateAccount:
    """Tests for POST /accounts."""

    async def test_create_account_success(self, client, platform_keypair):
        """Platform can create an account."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "create_account",
                "agent_id": "a-test-agent",
                "initial_balance": 50,
            },
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 201
        data = response.json()
        assert data["account_id"] == "a-test-agent"
        assert data["balance"] == 50
        assert "created_at" in data

    async def test_create_account_zero_balance(self, client, platform_keypair):
        """Account can be created with zero initial balance."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "create_account",
                "agent_id": "a-test-agent",
                "initial_balance": 0,
            },
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 201
        assert response.json()["balance"] == 0

    async def test_create_duplicate_account(self, client, platform_keypair):
        """Duplicate account returns 409."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "create_account",
                "agent_id": "a-test-agent",
                "initial_balance": 50,
            },
        )

        await client.post("/accounts", json={"token": token})
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 409
        assert response.json()["error"] == "ACCOUNT_EXISTS"

    async def test_create_account_agent_not_found(self, client, platform_keypair):
        """Account for non-existent agent returns 404."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, agent_exists=False)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-nonexistent", "initial_balance": 0},
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 404
        assert response.json()["error"] == "AGENT_NOT_FOUND"

    async def test_create_account_non_platform_forbidden(self, client, agent_keypair):
        """Non-platform agent cannot create accounts."""
        state = get_app_state()
        private_key, _ = agent_keypair
        agent_id = "a-regular-agent"

        async def mock_verify_jws(_token: str) -> dict[str, Any]:
            return {
                "valid": True,
                "agent_id": agent_id,
                "payload": {"action": "create_account", "agent_id": "a-victim"},
            }

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)

        token = make_jws_token(
            private_key,
            agent_id,
            {"action": "create_account", "agent_id": "a-victim", "initial_balance": 0},
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_create_account_missing_token(self, client):
        """Missing token returns 400."""
        response = await client.post("/accounts", json={})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
class TestCreditAccount:
    """Tests for POST /accounts/{account_id}/credit."""

    async def test_credit_success(self, client, platform_keypair):
        """Platform can credit an account."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)

        private_key, _ = platform_keypair

        # Create account first
        create_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "create_account",
                "agent_id": "a-test-agent",
                "initial_balance": 50,
            },
        )
        await client.post("/accounts", json={"token": create_token})

        # Credit it
        credit_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "account_id": "a-test-agent",
                "amount": 10,
                "reference": "salary_round_1",
            },
        )
        response = await client.post(
            "/accounts/a-test-agent/credit",
            json={"token": credit_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["balance_after"] == 60
        assert data["tx_id"].startswith("tx-")

    async def test_credit_account_not_found(self, client, platform_keypair):
        """Credit to non-existent account returns 404."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "amount": 10, "reference": "test"},
        )
        response = await client.post(
            "/accounts/a-nonexistent/credit",
            json={"token": token},
        )
        assert response.status_code == 404
        assert response.json()["error"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.unit
class TestGetBalance:
    """Tests for GET /accounts/{account_id}."""

    async def test_get_balance_success(self, client, platform_keypair, agent_keypair):
        """Agent can check own balance."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, agent_id="a-alice")

        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair
        agent_id = "a-alice"

        # Create account as platform
        create_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": agent_id, "initial_balance": 100},
        )
        await client.post("/accounts", json={"token": create_token})

        # Agent reads own balance
        async def mock_verify_agent(_token: str) -> dict[str, Any]:
            return {
                "valid": True,
                "agent_id": agent_id,
                "payload": {"action": "get_balance"},
            }

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_agent)

        balance_token = make_jws_token(
            agent_key, agent_id, {"action": "get_balance", "account_id": agent_id}
        )
        response = await client.get(
            f"/accounts/{agent_id}",
            headers={"Authorization": f"Bearer {balance_token}"},
        )
        assert response.status_code == 200
        assert response.json()["balance"] == 100

    async def test_get_balance_forbidden_other_account(self, client, agent_keypair):
        """Agent cannot read another agent's balance."""
        state = get_app_state()

        async def mock_verify(_token: str) -> dict[str, Any]:
            return {
                "valid": True,
                "agent_id": "a-eve",
                "payload": {"action": "get_balance"},
            }

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify)

        agent_key, _ = agent_keypair
        token = make_jws_token(agent_key, "a-eve", {"action": "get_balance"})
        response = await client.get(
            "/accounts/a-alice",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_get_balance_missing_auth_header(self, client):
        """Missing Authorization header returns 400."""
        response = await client.get("/accounts/a-test")
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
class TestGetTransactions:
    """Tests for GET /accounts/{account_id}/transactions."""

    async def test_get_transactions_success(self, client, platform_keypair, agent_keypair):
        """Agent can view own transaction history."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, agent_id="a-alice")

        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair
        agent_id = "a-alice"

        # Create account with initial balance
        create_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": agent_id, "initial_balance": 50},
        )
        await client.post("/accounts", json={"token": create_token})

        # Agent reads own transactions
        async def mock_verify_agent(_token: str) -> dict[str, Any]:
            return {
                "valid": True,
                "agent_id": agent_id,
                "payload": {"action": "get_transactions"},
            }

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_agent)

        tx_token = make_jws_token(
            agent_key, agent_id, {"action": "get_transactions", "account_id": agent_id}
        )
        response = await client.get(
            f"/accounts/{agent_id}/transactions",
            headers={"Authorization": f"Bearer {tx_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "transactions" in data
        assert len(data["transactions"]) == 1
        assert data["transactions"][0]["type"] == "credit"
        assert data["transactions"][0]["amount"] == 50

"""Self-service account creation tests."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _decode_jws_token(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parts = token.split(".")

    header_b64 = parts[0]
    header_padding = 4 - len(header_b64) % 4
    if header_padding != 4:
        header_b64 += "=" * header_padding
    header = json.loads(base64.urlsafe_b64decode(header_b64))

    payload_b64 = parts[1]
    payload_padding = 4 - len(payload_b64) % 4
    if payload_padding != 4:
        payload_b64 += "=" * payload_padding
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))

    return header, payload


def _setup_identity_mock_for_agent(
    app_state: Any,
    agent_exists: bool = True,
    agent_id: str = "a-self-service-agent",
) -> None:
    """Configure the mock identity client for self-service account operations."""

    async def mock_verify_jws(token: str) -> dict[str, Any]:
        header, payload = _decode_jws_token(token)
        return {"valid": True, "agent_id": header["kid"], "payload": payload}

    app_state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)

    if agent_exists:
        app_state.identity_client.get_agent = AsyncMock(
            return_value={"agent_id": agent_id, "name": "Test Agent"}
        )
    else:
        app_state.identity_client.get_agent = AsyncMock(return_value=None)


def _setup_identity_mock_for_platform(
    app_state: Any,
    agent_exists: bool = True,
    agent_id: str = "a-test-agent",
) -> None:
    """Configure the mock identity client for platform account creation."""
    _setup_identity_mock_for_agent(app_state, agent_exists=agent_exists, agent_id=agent_id)


@pytest.mark.unit
class TestAgentSelfServiceAccountCreation:
    """Tests for self-service POST /accounts behavior."""

    async def test_agent_creates_own_account_with_zero_balance(self, client, agent_keypair):
        state = get_app_state()
        agent_id = "a-self-service-agent"
        _setup_identity_mock_for_agent(state, agent_id=agent_id)

        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            agent_id,
            {
                "action": "create_account",
                "agent_id": agent_id,
                "initial_balance": 0,
            },
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 201
        data = response.json()
        assert data["account_id"] == agent_id
        assert data["balance"] == 0
        assert "created_at" in data

    async def test_agent_cannot_create_account_for_another_agent(self, client, agent_keypair):
        state = get_app_state()
        agent_id = "a-self-service-agent"
        _setup_identity_mock_for_agent(state, agent_id=agent_id)

        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            agent_id,
            {
                "action": "create_account",
                "agent_id": "a-someone-else",
                "initial_balance": 0,
            },
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_agent_cannot_create_account_with_nonzero_balance(self, client, agent_keypair):
        state = get_app_state()
        agent_id = "a-self-service-agent"
        _setup_identity_mock_for_agent(state, agent_id=agent_id)

        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            agent_id,
            {
                "action": "create_account",
                "agent_id": agent_id,
                "initial_balance": 100,
            },
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_agent_duplicate_account_returns_409(self, client, agent_keypair):
        state = get_app_state()
        agent_id = "a-self-service-agent"
        _setup_identity_mock_for_agent(state, agent_id=agent_id)

        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            agent_id,
            {
                "action": "create_account",
                "agent_id": agent_id,
                "initial_balance": 0,
            },
        )

        first_response = await client.post("/accounts", json={"token": token})
        assert first_response.status_code == 201

        second_response = await client.post("/accounts", json={"token": token})
        assert second_response.status_code == 409
        assert second_response.json()["error"] == "ACCOUNT_EXISTS"

    async def test_agent_not_found_in_identity_returns_404(self, client, agent_keypair):
        state = get_app_state()
        agent_id = "a-self-service-agent"
        _setup_identity_mock_for_agent(state, agent_exists=False, agent_id=agent_id)

        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            agent_id,
            {
                "action": "create_account",
                "agent_id": agent_id,
                "initial_balance": 0,
            },
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 404
        assert response.json()["error"] == "AGENT_NOT_FOUND"

    async def test_platform_still_creates_accounts_with_balance(self, client, platform_keypair):
        state = get_app_state()
        _setup_identity_mock_for_platform(state, agent_id="a-test-agent")

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "create_account",
                "agent_id": "a-test-agent",
                "initial_balance": 500,
            },
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 201
        assert response.json()["balance"] == 500

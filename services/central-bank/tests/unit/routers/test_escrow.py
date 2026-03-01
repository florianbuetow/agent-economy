"""Escrow endpoint tests."""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _setup_identity_mock(state: Any) -> None:
    """Configure mock identity client that decodes tokens."""

    async def mock_verify_jws(token: str) -> dict[str, Any]:
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

    state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)
    state.identity_client.get_agent = AsyncMock(
        return_value={"agent_id": "a-test-agent", "name": "Test"}
    )


async def _create_funded_account(
    client: Any,
    platform_keypair: Any,
    account_id: str,
    balance: int,
) -> None:
    """Helper to create a funded account via the API."""
    private_key, _ = platform_keypair
    token = make_jws_token(
        private_key,
        PLATFORM_AGENT_ID,
        {"action": "create_account", "agent_id": account_id, "initial_balance": balance},
    )
    resp = await client.post("/accounts", json={"token": token})
    assert resp.status_code == 201


@pytest.mark.unit
class TestEscrowLock:
    """Tests for POST /escrow/lock."""

    async def test_escrow_lock_success(self, client, platform_keypair, agent_keypair):
        """Agent can lock own funds in escrow."""
        state = get_app_state()
        _setup_identity_mock(state)

        await _create_funded_account(client, platform_keypair, "a-payer", 100)

        agent_key, _ = agent_keypair
        token = make_jws_token(
            agent_key,
            "a-payer",
            {
                "action": "escrow_lock",
                "agent_id": "a-payer",
                "amount": 30,
                "task_id": "T-001",
            },
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 201
        data = response.json()
        assert data["escrow_id"].startswith("esc-")
        assert data["amount"] == 30
        assert data["task_id"] == "T-001"
        assert data["status"] == "locked"

    async def test_escrow_lock_insufficient_funds(self, client, platform_keypair, agent_keypair):
        """Escrow lock with insufficient funds returns 402."""
        state = get_app_state()
        _setup_identity_mock(state)

        await _create_funded_account(client, platform_keypair, "a-payer", 10)

        agent_key, _ = agent_keypair
        token = make_jws_token(
            agent_key,
            "a-payer",
            {
                "action": "escrow_lock",
                "agent_id": "a-payer",
                "amount": 100,
                "task_id": "T-001",
            },
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 402
        assert response.json()["error"] == "INSUFFICIENT_FUNDS"

    async def test_escrow_lock_wrong_agent_forbidden(self, client, platform_keypair, agent_keypair):
        """Agent cannot lock another agent's funds."""
        state = get_app_state()
        _setup_identity_mock(state)

        await _create_funded_account(client, platform_keypair, "a-victim", 100)

        agent_key, _ = agent_keypair

        # Eve tries to lock victim's funds but signs as a-eve
        async def mock_verify_eve(_token: str) -> dict[str, Any]:
            return {
                "valid": True,
                "agent_id": "a-eve",
                "payload": {
                    "action": "escrow_lock",
                    "agent_id": "a-victim",
                    "amount": 50,
                    "task_id": "T-001",
                },
            }

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_eve)

        token = make_jws_token(
            agent_key,
            "a-eve",
            {
                "action": "escrow_lock",
                "agent_id": "a-victim",
                "amount": 50,
                "task_id": "T-001",
            },
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
class TestEscrowRelease:
    """Tests for POST /escrow/{escrow_id}/release."""

    async def test_escrow_release_success(self, client, platform_keypair, agent_keypair):
        """Platform can release escrowed funds to recipient."""
        state = get_app_state()
        _setup_identity_mock(state)

        await _create_funded_account(client, platform_keypair, "a-payer", 100)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)

        # Lock funds
        agent_key, _ = agent_keypair
        lock_token = make_jws_token(
            agent_key,
            "a-payer",
            {
                "action": "escrow_lock",
                "agent_id": "a-payer",
                "amount": 30,
                "task_id": "T-001",
            },
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        escrow_id = lock_resp.json()["escrow_id"]

        # Release as platform
        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": "a-worker",
            },
        )
        response = await client.post(
            f"/escrow/{escrow_id}/release",
            json={"token": release_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "released"
        assert data["amount"] == 30
        assert data["recipient"] == "a-worker"

    async def test_escrow_release_already_resolved(self, client, platform_keypair, agent_keypair):
        """Releasing already-resolved escrow returns 409."""
        state = get_app_state()
        _setup_identity_mock(state)

        await _create_funded_account(client, platform_keypair, "a-payer", 100)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)

        agent_key, _ = agent_keypair
        lock_token = make_jws_token(
            agent_key,
            "a-payer",
            {
                "action": "escrow_lock",
                "agent_id": "a-payer",
                "amount": 30,
                "task_id": "T-001",
            },
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        escrow_id = lock_resp.json()["escrow_id"]

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": escrow_id,
                "recipient_account_id": "a-worker",
            },
        )
        await client.post(f"/escrow/{escrow_id}/release", json={"token": release_token})

        # Try to release again
        response = await client.post(f"/escrow/{escrow_id}/release", json={"token": release_token})
        assert response.status_code == 409
        assert response.json()["error"] == "ESCROW_ALREADY_RESOLVED"

    async def test_escrow_not_found(self, client, platform_keypair):
        """Release of non-existent escrow returns 404."""
        state = get_app_state()
        _setup_identity_mock(state)

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": "esc-fake",
                "recipient_account_id": "a-worker",
            },
        )
        response = await client.post(
            "/escrow/esc-fake/release",
            json={"token": release_token},
        )
        assert response.status_code == 404
        assert response.json()["error"] == "ESCROW_NOT_FOUND"


@pytest.mark.unit
class TestEscrowSplit:
    """Tests for POST /escrow/{escrow_id}/split."""

    async def test_escrow_split_success(self, client, platform_keypair, agent_keypair):
        """Platform can split escrowed funds between worker and poster."""
        state = get_app_state()
        _setup_identity_mock(state)

        await _create_funded_account(client, platform_keypair, "a-poster", 100)
        await _create_funded_account(client, platform_keypair, "a-worker", 0)

        # Lock funds
        agent_key, _ = agent_keypair
        lock_token = make_jws_token(
            agent_key,
            "a-poster",
            {
                "action": "escrow_lock",
                "agent_id": "a-poster",
                "amount": 100,
                "task_id": "T-001",
            },
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        escrow_id = lock_resp.json()["escrow_id"]

        # Split as platform: 40% to worker, 60% back to poster
        platform_key, _ = platform_keypair
        split_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": "a-worker",
                "worker_pct": 40,
                "poster_account_id": "a-poster",
            },
        )
        response = await client.post(
            f"/escrow/{escrow_id}/split",
            json={"token": split_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "split"
        assert data["worker_amount"] == 40
        assert data["poster_amount"] == 60

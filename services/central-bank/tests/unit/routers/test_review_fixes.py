"""Tests for code review fix findings."""

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


@pytest.mark.unit
class TestJWSVerificationFailure:
    """Tests for valid:false propagation from Identity service."""

    async def test_create_account_invalid_jws_returns_403(self, client, platform_keypair):
        """Invalid JWS signature returns 403 FORBIDDEN."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            return_value={"valid": False, "reason": "signature mismatch"}
        )

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test", "initial_balance": 0},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_escrow_lock_invalid_jws_returns_403(self, client, agent_keypair):
        """Invalid JWS on escrow lock returns 403."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            return_value={"valid": False, "reason": "signature mismatch"}
        )

        agent_key, _ = agent_keypair
        token = make_jws_token(
            agent_key,
            "a-agent",
            {"action": "escrow_lock", "agent_id": "a-agent", "amount": 10, "task_id": "T-1"},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_credit_invalid_jws_returns_403(self, client, platform_keypair):
        """Invalid JWS on credit returns 403."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            return_value={"valid": False, "reason": "signature mismatch"}
        )

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-test", "amount": 10, "reference": "test"},
        )
        response = await client.post("/accounts/a-test/credit", json={"token": token})
        assert response.status_code == 403

    async def test_get_balance_invalid_jws_returns_403(self, client, agent_keypair):
        """Invalid JWS on balance check returns 403."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            return_value={"valid": False, "reason": "signature mismatch"}
        )

        agent_key, _ = agent_keypair
        token = make_jws_token(agent_key, "a-agent", {"action": "get_balance"})
        response = await client.get(
            "/accounts/a-agent",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


@pytest.mark.unit
class TestPayloadMismatch:
    """Tests for URL-vs-payload cross-check."""

    async def test_credit_payload_mismatch_returns_400(self, client, platform_keypair):
        """Credit with mismatched URL and payload account_id returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        private_key, _ = platform_keypair
        # Token says account_id is "a-alice" but URL says "a-bob"
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-alice", "amount": 10, "reference": "test"},
        )
        response = await client.post("/accounts/a-bob/credit", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "PAYLOAD_MISMATCH"

    async def test_escrow_release_payload_mismatch_returns_400(self, client, platform_keypair):
        """Release with mismatched URL and payload escrow_id returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": "esc-real",
                "recipient_account_id": "a-worker",
            },
        )
        response = await client.post(
            "/escrow/esc-fake/release",
            json={"token": token},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "PAYLOAD_MISMATCH"

    async def test_escrow_split_payload_mismatch_returns_400(self, client, platform_keypair):
        """Split with mismatched URL and payload escrow_id returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_split",
                "escrow_id": "esc-real",
                "worker_account_id": "a-worker",
                "worker_pct": 50,
                "poster_account_id": "a-poster",
            },
        )
        response = await client.post(
            "/escrow/esc-fake/split",
            json={"token": token},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "PAYLOAD_MISMATCH"


@pytest.mark.unit
class TestMissingRequiredFields:
    """Tests for required fields that used to have silent defaults."""

    async def test_create_account_missing_initial_balance(self, client, platform_keypair):
        """Missing initial_balance in payload returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test"},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 400

    async def test_credit_missing_reference(self, client, platform_keypair):
        """Missing reference in credit payload returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        # Create account first
        create_token = make_jws_token(
            private_key := platform_keypair[0],
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test", "initial_balance": 100},
        )
        await client.post("/accounts", json={"token": create_token})

        # Try credit without reference
        credit_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-test", "amount": 10},
        )
        response = await client.post("/accounts/a-test/credit", json={"token": credit_token})
        assert response.status_code == 400

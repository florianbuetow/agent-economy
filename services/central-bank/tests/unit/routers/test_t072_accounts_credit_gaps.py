"""T-072 coverage-sweep gap fixes: POST /accounts and POST /accounts/{id}/credit
payload-validation scenarios from central-bank-service-auth-tests.md and
central-bank-service-tests.md that had no existing test.

Uses the same mocked-Identity `client`/`platform_keypair`/`agent_keypair` fixtures
as the existing account/credit router tests (see tests/unit/routers/conftest.py).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.exceptions import InvalidSignature

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _setup_identity_mock_for_platform(
    app_state: Any,
    agent_exists: bool = True,
    agent_id: str = "a-test-agent",
) -> None:
    """Configure the mock identity client for platform account creation."""
    if agent_exists:
        app_state.identity_client.get_agent = AsyncMock(
            return_value={"agent_id": agent_id, "name": "Test Agent"}
        )
    else:
        app_state.identity_client.get_agent = AsyncMock(return_value=None)


@pytest.mark.unit
class TestCreateAccountBodyTokenValidation:
    """AUTH-05, AUTH-06: body `token` field shape checks on POST /accounts."""

    async def test_null_token_returns_invalid_jws(self, client):
        """AUTH-05: Null `token` in POST body returns 400 invalid_jws."""
        response = await client.post("/accounts", json={"token": None})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_jws"

    async def test_non_string_token_returns_invalid_jws(self, client):
        """AUTH-06: Non-string `token` (integer) in POST body returns 400 invalid_jws."""
        response = await client.post("/accounts", json={"token": 12345})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_jws"


@pytest.mark.unit
class TestCreateAccountMalformedJsonBody:
    """AUTH-17, AUTH-18, ACC-12: request body is not a valid JSON object."""

    async def test_malformed_json_body_returns_invalid_json(self, client):
        """AUTH-17 / ACC-12: malformed (non-JSON) body returns 400 invalid_json."""
        response = await client.post(
            "/accounts",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_json"

    async def test_json_array_body_returns_invalid_json(self, client):
        """AUTH-18: a valid-JSON-but-non-object (array) body returns 400 invalid_json."""
        response = await client.post(
            "/accounts",
            content=b'[{"token": "eyJ..."}]',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_json"


@pytest.mark.unit
class TestCreateAccountPayloadShape:
    """AUTH-14, AUTH-15, ACC-09, ACC-11: JWS payload `action`/`agent_id` shape checks."""

    async def test_wrong_action_returns_invalid_payload(self, client, platform_keypair):
        """AUTH-14 / ACC-11: wrong `action` value in JWS payload returns invalid_payload."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "escrow_lock", "agent_id": "a-xxx", "initial_balance": 50},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_missing_action_returns_invalid_payload(self, client, platform_keypair):
        """AUTH-15: missing `action` field in JWS payload returns invalid_payload."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"agent_id": "a-xxx", "initial_balance": 50},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_missing_agent_id_returns_invalid_payload(self, client, platform_keypair):
        """ACC-09: missing agent_id in JWS payload returns invalid_payload."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "initial_balance": 10},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_missing_initial_balance_returns_invalid_payload(self, client, platform_keypair):
        """ACC-10: missing initial_balance in JWS payload returns invalid_payload
        (strengthens test_review_fixes.py's status-only check with the error code)."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test"},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"


@pytest.mark.unit
class TestCreateAccountInvalidAmount:
    """ACC-07, ACC-08: `initial_balance` range/type checks."""

    async def test_negative_initial_balance_returns_invalid_amount(self, client, platform_keypair):
        """ACC-07: negative initial_balance is rejected with invalid_amount."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": -1},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_amount"

    async def test_non_integer_initial_balance_returns_invalid_amount(
        self, client, platform_keypair
    ):
        """ACC-08: non-integer initial_balance is rejected with invalid_amount."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": 10.5},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_amount"


@pytest.mark.unit
class TestCreateAccountSignatureBeatsPayload:
    """PREC-06: on create_account, Identity's signature verdict is checked before
    the payload shape (verify-first ordering, unlike the local platform tier)."""

    async def test_invalid_signature_reported_even_with_malformed_payload(
        self, client, agent_keypair
    ):
        """PREC-06: a tampered/invalid JWS (per Identity) returns 403 forbidden even
        though the payload is separately missing agent_id — signature check wins."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(side_effect=InvalidSignature())
        private_key, _ = agent_keypair
        token = make_jws_token(
            private_key,
            "a-alice",
            {"action": "create_account", "initial_balance": 10},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"


@pytest.mark.unit
class TestCreditAmountValidation:
    """CR-07, CR-08: `amount` range checks on credit."""

    async def test_zero_amount_returns_invalid_amount(self, client, platform_keypair):
        """CR-07: zero credit amount is rejected with invalid_amount."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "account_id": "a-test-agent",
                "amount": 0,
                "reference": "zero_credit",
            },
        )
        response = await client.post("/accounts/a-test-agent/credit", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_amount"

    async def test_negative_amount_returns_invalid_amount(self, client, platform_keypair):
        """CR-08: negative credit amount is rejected with invalid_amount."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "account_id": "a-test-agent",
                "amount": -10,
                "reference": "neg_credit",
            },
        )
        response = await client.post("/accounts/a-test-agent/credit", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_amount"


@pytest.mark.unit
class TestCreditPayloadShape:
    """CR-09, CR-11: missing `reference` / wrong `action` on credit."""

    async def test_missing_reference_returns_invalid_payload(self, client, platform_keypair):
        """CR-09: missing reference in credit JWS payload returns invalid_payload
        (strengthens test_review_fixes.py's status-only check with the error code)."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-test-agent", "amount": 10},
        )
        response = await client.post("/accounts/a-test-agent/credit", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"

    async def test_wrong_action_returns_invalid_payload(self, client, platform_keypair):
        """CR-11: wrong action ("create_account") on credit endpoint returns invalid_payload."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "create_account",
                "account_id": "a-test-agent",
                "amount": 10,
                "reference": "wrong_action",
            },
        )
        response = await client.post("/accounts/a-test-agent/credit", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_payload"


@pytest.mark.unit
class TestCreditIdempotencyAndConflict:
    """CR-02, CR-04: repeated/conflicting credit references through the HTTP endpoint."""

    async def test_multiple_credits_accumulate(self, client, platform_keypair):
        """CR-02: multiple distinct credits accumulate correctly."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair

        create_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": 0},
        )
        await client.post("/accounts", json={"token": create_token})

        first_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "account_id": "a-test-agent",
                "amount": 30,
                "reference": "bonus_1",
            },
        )
        first = await client.post("/accounts/a-test-agent/credit", json={"token": first_token})
        assert first.status_code == 200
        assert first.json()["balance_after"] == 30

        second_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "account_id": "a-test-agent",
                "amount": 20,
                "reference": "bonus_2",
            },
        )
        second = await client.post("/accounts/a-test-agent/credit", json={"token": second_token})
        assert second.status_code == 200
        assert second.json()["balance_after"] == 50

    async def test_duplicate_reference_different_amount_returns_payload_mismatch(
        self, client, platform_keypair
    ):
        """CR-04: duplicate credit reference with a different amount returns payload_mismatch."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair

        create_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": 0},
        )
        await client.post("/accounts", json={"token": create_token})

        first_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "account_id": "a-test-agent",
                "amount": 25,
                "reference": "salary_round_1",
            },
        )
        first = await client.post("/accounts/a-test-agent/credit", json={"token": first_token})
        assert first.status_code == 200

        second_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "credit",
                "account_id": "a-test-agent",
                "amount": 30,
                "reference": "salary_round_1",
            },
        )
        second = await client.post("/accounts/a-test-agent/credit", json={"token": second_token})
        assert second.status_code == 400
        assert second.json()["error"] == "payload_mismatch"

    async def test_idempotent_credit_returns_same_tx_id(self, client, platform_keypair):
        """CR-03: an identical repeated credit request (same reference/amount) through
        the HTTP endpoint returns the original tx_id and does not double-credit."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state)
        private_key, _ = platform_keypair

        create_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": 0},
        )
        await client.post("/accounts", json={"token": create_token})

        credit_payload = {
            "action": "credit",
            "account_id": "a-test-agent",
            "amount": 25,
            "reference": "salary_round_1",
        }
        first_token = make_jws_token(private_key, PLATFORM_AGENT_ID, credit_payload)
        first = await client.post("/accounts/a-test-agent/credit", json={"token": first_token})
        assert first.status_code == 200

        second_token = make_jws_token(private_key, PLATFORM_AGENT_ID, credit_payload)
        second = await client.post("/accounts/a-test-agent/credit", json={"token": second_token})
        assert second.status_code == 200
        assert second.json()["tx_id"] == first.json()["tx_id"]
        assert second.json()["balance_after"] == first.json()["balance_after"] == 25

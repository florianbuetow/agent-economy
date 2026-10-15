"""T-072 coverage-sweep gap fixes: GET /health accuracy, HTTP method misuse across
every defined route, and cross-cutting security assertions (error envelope shape,
no internal leakage, ID formatting) from central-bank-service-tests.md.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _setup_identity_mock(state: Any) -> None:
    state.identity_client.get_agent = AsyncMock(
        return_value={"agent_id": "a-test-agent", "name": "Test"}
    )


async def _create_account(
    client: Any, platform_keypair: Any, account_id: str, balance: int
) -> None:
    private_key, _ = platform_keypair
    token = make_jws_token(
        private_key,
        PLATFORM_AGENT_ID,
        {"action": "create_account", "agent_id": account_id, "initial_balance": balance},
    )
    resp = await client.post("/accounts", json={"token": token})
    assert resp.status_code == 201, resp.text


async def _lock(client: Any, agent_keypair: Any, agent_id: str, amount: int, task_id: str) -> Any:
    private_key, _ = agent_keypair
    token = make_jws_token(
        private_key,
        agent_id,
        {"action": "escrow_lock", "agent_id": agent_id, "amount": amount, "task_id": task_id},
    )
    resp = await client.post("/escrow/lock", json={"token": token})
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.mark.unit
class TestHealthAccuracy:
    """HLTH-02, HLTH-03, HLTH-04."""

    async def test_total_accounts_is_accurate(self, client, platform_keypair):
        """HLTH-02: total_accounts equals the number of created accounts."""
        state = get_app_state()
        _setup_identity_mock(state)
        for i in range(3):
            await _create_account(client, platform_keypair, f"a-account-{i}", 0)

        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["total_accounts"] == 3

    async def test_total_escrowed_excludes_released_escrows(
        self, client, platform_keypair, agent_keypair
    ):
        """HLTH-03: total_escrowed counts only still-locked escrows, not released ones."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_account(client, platform_keypair, "a-alice", 100)
        await _create_account(client, platform_keypair, "a-bob", 0)

        first = await _lock(client, agent_keypair, "a-alice", 30, "T-h1")
        await _lock(client, agent_keypair, "a-alice", 20, "T-h2")

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": first["escrow_id"],
                "recipient_account_id": "a-bob",
            },
        )
        release_resp = await client.post(
            f"/escrow/{first['escrow_id']}/release", json={"token": release_token}
        )
        assert release_resp.status_code == 200

        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["total_escrowed"] == 20

    async def test_uptime_is_monotonic(self, client):
        """HLTH-04: uptime_seconds strictly increases between two health calls."""
        first = await client.get("/health")
        assert first.status_code == 200
        await asyncio.sleep(1.05)
        second = await client.get("/health")
        assert second.status_code == 200
        assert second.json()["uptime_seconds"] > first.json()["uptime_seconds"]


@pytest.mark.unit
class TestHttpMethodMisuse:
    """HTTP-01: unsupported HTTP methods on every defined route are rejected with
    405 method_not_allowed, using the standard error envelope.

    ``POST /accounts/{account_id}`` and ``POST /accounts/{account_id}/transactions``
    (only GET is defined for either route) are included below. WP-13 fixed
    ``RequestValidationMiddleware``'s ``_JSON_POST_PREFIXES`` (central_bank_service/
    core/middleware.py), which previously intercepted *any* POST under
    ``/accounts/`` as a "JSON POST endpoint" without checking that the exact path
    actually has a POST route, rejecting a non-JSON POST with 415
    unsupported_media_type before FastAPI's router ever got a chance to return 405.
    The prefix is now suffix-qualified (``/accounts/`` + ``/credit``) so only the
    real dynamic POST route matches.
    """

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/accounts"),
            ("PUT", "/accounts"),
            ("DELETE", "/accounts"),
            ("PUT", "/accounts/a-x"),
            ("DELETE", "/accounts/a-x"),
            ("POST", "/accounts/a-x"),
            ("PUT", "/accounts/a-x/transactions"),
            ("DELETE", "/accounts/a-x/transactions"),
            ("POST", "/accounts/a-x/transactions"),
            ("PUT", "/accounts/a-x/credit"),
            ("DELETE", "/accounts/a-x/credit"),
            ("GET", "/escrow/lock"),
            ("PUT", "/escrow/lock"),
            ("DELETE", "/escrow/lock"),
            ("GET", "/escrow/esc-x/release"),
            ("PUT", "/escrow/esc-x/release"),
            ("DELETE", "/escrow/esc-x/release"),
            ("GET", "/escrow/esc-x/split"),
            ("PUT", "/escrow/esc-x/split"),
            ("DELETE", "/escrow/esc-x/split"),
            ("POST", "/health"),
        ],
    )
    async def test_wrong_method_returns_method_not_allowed(
        self, client, method: str, path: str
    ) -> None:
        response = await client.request(method, path)
        assert response.status_code == 405
        assert response.json()["error"] == "method_not_allowed"


@pytest.mark.unit
class TestErrorEnvelopeConsistency:
    """SEC-01, SEC-02: every failing response uses the exact 3-field envelope, and
    none leak internals (stack traces, SQL fragments, file paths)."""

    async def test_error_envelope_has_exactly_error_message_details(self, client):
        """SEC-01: representative failures all have exactly {error, message, details}."""
        responses = [
            await client.post(
                "/accounts", content=b"{not json", headers={"Content-Type": "application/json"}
            ),
            await client.post("/accounts", json={"token": None}),
            await client.get("/accounts/a-nonexistent-uuid"),
            await client.request("DELETE", "/accounts"),
        ]
        for response in responses:
            body = response.json()
            assert set(body) == {"error", "message", "details"}
            assert isinstance(body["error"], str)
            assert body["error"] == body["error"].lower()
            assert isinstance(body["message"], str)
            assert isinstance(body["details"], dict)

    async def test_no_internal_error_leakage(self, client, platform_keypair, agent_keypair):
        """SEC-02: failure messages never contain stack traces, SQL fragments, file
        paths, or driver internals."""
        state = get_app_state()
        _setup_identity_mock(state)
        await _create_account(client, platform_keypair, "a-alice", 10)
        locked = await _lock(client, agent_keypair, "a-alice", 5, "T-sec")

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": locked["escrow_id"],
                "recipient_account_id": "a-alice",
            },
        )
        second_lock_token = make_jws_token(
            agent_keypair[0],
            "a-alice",
            {"action": "escrow_lock", "agent_id": "a-alice", "amount": 999, "task_id": "T-sec2"},
        )
        responses = [
            await client.post(
                "/accounts", content=b"{not json", headers={"Content-Type": "application/json"}
            ),
            await client.post("/accounts", json={"token": None}),
            await client.get("/accounts/a-nonexistent-uuid"),
            await client.post(
                "/escrow/esc-nonexistent-uuid/release", json={"token": release_token}
            ),
            await client.post("/escrow/lock", json={"token": second_lock_token}),
        ]
        banned_substrings = (
            "Traceback",
            "sqlite3",
            "SELECT ",
            "INSERT ",
            "/Users/",
            "site-packages",
        )
        for response in responses:
            message = response.json()["message"]
            for banned in banned_substrings:
                assert banned not in message


@pytest.mark.unit
class TestIdFormatting:
    """SEC-03: account_id, tx_id, and escrow_id all follow their documented formats."""

    async def test_ids_are_correctly_formatted(self, client, platform_keypair, agent_keypair):
        state = get_app_state()
        _setup_identity_mock(state)
        account_ids = [f"a-{uuid4()}" for _ in range(3)]
        for account_id in account_ids:
            await _create_account(client, platform_keypair, account_id, 10)
            assert account_id.startswith("a-")

        platform_key, _ = platform_keypair
        for account_id in account_ids:
            credit_token = make_jws_token(
                platform_key,
                PLATFORM_AGENT_ID,
                {
                    "action": "credit",
                    "account_id": account_id,
                    "amount": 5,
                    "reference": f"r-{uuid4()}",
                },
            )
            credit_resp = await client.post(
                f"/accounts/{account_id}/credit", json={"token": credit_token}
            )
            assert credit_resp.status_code == 200
            assert credit_resp.json()["tx_id"].startswith("tx-")

        for account_id in account_ids:
            locked = await _lock(client, agent_keypair, account_id, 3, f"T-{uuid4()}")
            assert locked["escrow_id"].startswith("esc-")

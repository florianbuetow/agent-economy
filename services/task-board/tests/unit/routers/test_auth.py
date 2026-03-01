"""Authentication and authorization edge-case tests for the Task Board service."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from task_board_service.core.state import get_app_state
from tests.helpers import make_jws_token, tamper_jws
from tests.unit.routers.conftest import (
    create_task,
    make_task_id,
    setup_task_in_execution,
    upload_asset,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

# Patterns that must never appear in error messages
_LEAK_PATTERNS = [
    re.compile(r"Traceback", re.IGNORECASE),
    re.compile(r"File\s+\"/"),
    re.compile(r"localhost:\d{4}"),
    re.compile(r"http://"),
    re.compile(r"private.?key", re.IGNORECASE),
    re.compile(r"Ed25519", re.IGNORECASE),
    re.compile(r"\.py\b"),
]


class TestBodyTokenEdgeCases:
    """Category 1: Body token edge cases (AUTH-01 to AUTH-13)."""

    @pytest.mark.unit
    async def test_null_tokens_on_task_creation(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-01: Null task_token and escrow_token on POST /tasks returns 400 INVALID_JWS."""
        resp = await client.post(
            "/tasks",
            json={"task_token": None, "escrow_token": None},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_null_token_on_single_token_endpoint(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-02: POST /tasks/{task_id}/bids with null token returns 400 INVALID_JWS."""
        task_id = make_task_id()
        resp = await client.post(
            f"/tasks/{task_id}/bids",
            json={"token": None},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_integer_token_in_body(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-03: POST /tasks/{task_id}/cancel with integer token returns 400 INVALID_JWS."""
        task_id = make_task_id()
        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": 12345},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_array_token_in_body(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-04: POST /tasks/{task_id}/bids with array token returns 400 INVALID_JWS."""
        task_id = make_task_id()
        resp = await client.post(
            f"/tasks/{task_id}/bids",
            json={"token": ["eyJ..."]},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_object_token_in_body(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-05: POST /tasks/{task_id}/submit with object token returns 400 INVALID_JWS."""
        task_id = make_task_id()
        resp = await client.post(
            f"/tasks/{task_id}/submit",
            json={"token": {"jws": "eyJ..."}},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_boolean_token_in_body(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-06: POST /tasks/{task_id}/approve with boolean token returns 400 INVALID_JWS."""
        task_id = make_task_id()
        resp = await client.post(
            f"/tasks/{task_id}/approve",
            json={"token": True},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_missing_action_field_in_jws_cancel(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """AUTH-07: JWS payload missing action field on cancel returns 400 INVALID_PAYLOAD."""
        task_id = make_task_id()
        private_key = alice_keypair[0]
        # Payload has no "action" field
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"task_id": task_id, "poster_id": alice_agent_id},
        )

        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": token},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_missing_action_field_in_jws_ruling(
        self,
        client: AsyncClient,
        platform_keypair: tuple[Ed25519PrivateKey, str],
        platform_agent_id: str,
    ) -> None:
        """AUTH-08: JWS payload missing action field on ruling returns 400 INVALID_PAYLOAD."""
        task_id = make_task_id()
        private_key = platform_keypair[0]
        # Payload has no "action" field
        token = make_jws_token(
            private_key,
            platform_agent_id,
            {"task_id": task_id, "worker_pct": 50, "ruling_summary": "Test ruling"},
        )

        resp = await client.post(
            f"/tasks/{task_id}/ruling",
            json={"token": token},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_array_body_on_single_token_endpoint(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-09: Array JSON body on cancel endpoint returns 400 INVALID_JSON."""
        task_id = make_task_id()
        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            content=b'[{"token": "eyJ..."}]',
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JSON"

    @pytest.mark.unit
    async def test_string_body_on_single_token_endpoint(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-10: String JSON body on bids endpoint returns 400 INVALID_JSON."""
        task_id = make_task_id()
        resp = await client.post(
            f"/tasks/{task_id}/bids",
            content=b'"just a string"',
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JSON"

    @pytest.mark.unit
    async def test_array_body_on_dual_token_endpoint(
        self,
        client: AsyncClient,
    ) -> None:
        """AUTH-11: Array JSON body on task creation returns 400 INVALID_JSON."""
        resp = await client.post(
            "/tasks",
            content=b'[{"task_token": "eyJ...", "escrow_token": "eyJ..."}]',
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JSON"

    @pytest.mark.unit
    async def test_null_task_token_with_valid_escrow_token(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """AUTH-12: Null task_token with valid escrow_token returns 400 INVALID_JWS."""
        task_id = make_task_id()
        private_key = alice_keypair[0]

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(private_key, alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": None, "escrow_token": escrow_token},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_valid_task_token_with_null_escrow_token(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """AUTH-13: Valid task_token with null escrow_token returns 400 INVALID_JWS."""
        task_id = make_task_id()
        private_key = alice_keypair[0]

        task_payload = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice_agent_id,
            "title": "Test task",
            "spec": "Test specification",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(private_key, alice_agent_id, task_payload)

        resp = await client.post(
            "/tasks",
            json={"task_token": task_token, "escrow_token": None},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"


class TestBearerTokenValidation:
    """Category 2: Bearer token validation (BEARER-01 to BEARER-13)."""

    @pytest.mark.unit
    async def test_valid_bearer_on_sealed_bids(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """BEARER-01: Valid Bearer token on GET /tasks/{id}/bids in OPEN status returns 200."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 200
        assert "bids" in resp.json()

    @pytest.mark.unit
    async def test_valid_bearer_on_asset_upload(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """BEARER-02: Valid Bearer token on POST /tasks/{id}/assets returns 201."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await upload_asset(client, bob_keypair, bob_agent_id, task_id)

        assert resp.status_code == 201
        data = resp.json()
        assert "asset_id" in data
        assert "filename" in data
        assert "content_hash" in data
        assert "uploaded_at" in data

    @pytest.mark.unit
    async def test_missing_authorization_header_sealed_bids(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """BEARER-03: Missing Authorization header on sealed bids returns 400 INVALID_JWS."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # GET without Authorization header
        resp = await client.get(f"/tasks/{task_id}/bids")

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_authorization_without_bearer_prefix(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """BEARER-04: Authorization without 'Bearer ' prefix returns 400 INVALID_JWS."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Token {token}"}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_empty_bearer_token(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """BEARER-05: Empty Bearer token returns 400 INVALID_JWS."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        headers = {"Authorization": "Bearer "}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_malformed_bearer_token(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """BEARER-06: Malformed Bearer token (not three-part JWS) returns 400 INVALID_JWS."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        headers = {"Authorization": "Bearer not-a-jws"}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    async def test_tampered_bearer_token(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """BEARER-07: Tampered Bearer token returns 403 FORBIDDEN."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        tampered = tamper_jws(token)
        headers = {"Authorization": f"Bearer {tampered}"}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_wrong_action_in_bearer_sealed_bids(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """BEARER-08: Wrong action in Bearer for sealed bids returns 400 INVALID_PAYLOAD."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "create_task", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_wrong_action_in_bearer_asset_upload(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """BEARER-09: Wrong action in Bearer for asset upload returns 400 INVALID_PAYLOAD."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        private_key = bob_keypair[0]
        token = make_jws_token(
            private_key,
            bob_agent_id,
            {"action": "submit_bid", "task_id": task_id},
        )

        resp = await client.post(
            f"/tasks/{task_id}/assets",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("test.txt", b"test content", "application/octet-stream")},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_task_id_mismatch_in_bearer_sealed_bids(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """BEARER-10: Payload task_id mismatch for sealed bids returns 400 INVALID_PAYLOAD."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": "t-different-uuid"},
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_task_id_mismatch_in_bearer_asset_upload(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """BEARER-11: Payload task_id mismatch for asset upload returns 400 INVALID_PAYLOAD."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        private_key = bob_keypair[0]
        token = make_jws_token(
            private_key,
            bob_agent_id,
            {"action": "upload_asset", "task_id": "t-different-uuid"},
        )

        resp = await client.post(
            f"/tasks/{task_id}/assets",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("test.txt", b"test content", "application/octet-stream")},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_non_poster_accessing_sealed_bids(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """BEARER-12: Non-poster accessing sealed bids returns 403 FORBIDDEN."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Bob (not the poster) tries to list sealed bids
        private_key = bob_keypair[0]
        token = make_jws_token(
            private_key,
            bob_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_non_worker_uploading_asset(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
        carol_keypair: tuple[Ed25519PrivateKey, str],
        carol_agent_id: str,
    ) -> None:
        """BEARER-13: Non-worker uploading asset returns 403 FORBIDDEN."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Carol (not the assigned worker) tries to upload
        private_key = carol_keypair[0]
        token = make_jws_token(
            private_key,
            carol_agent_id,
            {"action": "upload_asset", "task_id": task_id},
        )

        resp = await client.post(
            f"/tasks/{task_id}/assets",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("rogue.txt", b"rogue content", "application/octet-stream")},
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"


class TestIdentityDependency:
    """Category 3: Identity service dependency (IDEP-01 to IDEP-03)."""

    @pytest.mark.unit
    async def test_identity_timeout_returns_502(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """IDEP-01: Identity service timeout returns 502 IDENTITY_SERVICE_UNAVAILABLE."""
        # Configure identity mock to simulate timeout
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            side_effect=TimeoutError("Identity service timed out")
        )

        task_id = make_task_id()
        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "cancel_task", "task_id": task_id, "poster_id": alice_agent_id},
        )

        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": token},
        )

        assert resp.status_code == 502
        assert resp.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"

    @pytest.mark.unit
    async def test_identity_unexpected_response_returns_502(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """IDEP-02: Identity service non-JSON 500 returns 502 IDENTITY_SERVICE_UNAVAILABLE."""
        # Configure identity mock to simulate unexpected response
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            side_effect=ValueError("Unexpected response from Identity service")
        )

        task_id = make_task_id()
        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "submit_bid", "task_id": task_id, "bidder_id": alice_agent_id, "amount": 90},
        )

        resp = await client.post(
            f"/tasks/{task_id}/bids",
            json={"token": token},
        )

        assert resp.status_code == 502
        assert resp.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"

    @pytest.mark.unit
    async def test_identity_unexpected_response_on_bearer_endpoint(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """IDEP-03: Identity service unexpected response on Bearer endpoint returns 502."""
        # First create a task while identity mock is still working
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        # Now break the identity mock
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            side_effect=ValueError("Unexpected response from Identity service")
        )

        private_key = alice_keypair[0]
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": task_id},
        )
        headers = {"Authorization": f"Bearer {token}"}

        resp = await client.get(f"/tasks/{task_id}/bids", headers=headers)

        assert resp.status_code == 502
        assert resp.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"


class TestPublicEndpoints:
    """Category 4: Public endpoints requiring no authentication (PUB-01 to PUB-06)."""

    @pytest.mark.unit
    async def test_list_tasks_no_auth(
        self,
        client: AsyncClient,
    ) -> None:
        """PUB-01: GET /tasks requires no authentication and returns 200."""
        resp = await client.get("/tasks")

        assert resp.status_code == 200
        assert "tasks" in resp.json()

    @pytest.mark.unit
    async def test_get_task_no_auth(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """PUB-02: GET /tasks/{task_id} requires no authentication and returns 200."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        resp = await client.get(f"/tasks/{task_id}")

        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == task_id

    @pytest.mark.unit
    async def test_list_bids_no_auth_when_not_open(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """PUB-03: GET /tasks/{id}/bids with no auth when task is NOT OPEN returns 200."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # No Authorization header — task is past OPEN status
        resp = await client.get(f"/tasks/{task_id}/bids")

        assert resp.status_code == 200
        assert "bids" in resp.json()

    @pytest.mark.unit
    async def test_list_assets_no_auth(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """PUB-04: GET /tasks/{task_id}/assets requires no authentication and returns 200."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        resp = await client.get(f"/tasks/{task_id}/assets")

        assert resp.status_code == 200
        assert "assets" in resp.json()

    @pytest.mark.unit
    async def test_download_asset_no_auth(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """PUB-05: GET /tasks/{id}/assets/{asset_id} requires no auth and returns 200."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        upload_resp = await upload_asset(client, bob_keypair, bob_agent_id, task_id)
        assert upload_resp.status_code == 201
        asset_id = upload_resp.json()["asset_id"]

        # Download without any Authorization header
        resp = await client.get(f"/tasks/{task_id}/assets/{asset_id}")

        assert resp.status_code == 200

    @pytest.mark.unit
    async def test_health_no_auth(
        self,
        client: AsyncClient,
    ) -> None:
        """PUB-06: GET /health requires no authentication and returns 200."""
        resp = await client.get("/health")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestTokenReplay:
    """Category 5: Cross-service token replay (REPLAY-01 to REPLAY-03)."""

    @pytest.mark.unit
    async def test_central_bank_token_rejected(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """REPLAY-01: Central Bank escrow_lock token rejected on Task Board cancel."""
        task_id = make_task_id()
        private_key = alice_keypair[0]
        # Central Bank action used on Task Board endpoint
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {
                "action": "escrow_lock",
                "agent_id": alice_agent_id,
                "amount": 100,
                "task_id": task_id,
            },
        )

        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": token},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_court_token_rejected(
        self,
        client: AsyncClient,
        platform_keypair: tuple[Ed25519PrivateKey, str],
        platform_agent_id: str,
    ) -> None:
        """REPLAY-02: Court file_dispute token rejected on Task Board ruling."""
        task_id = make_task_id()
        private_key = platform_keypair[0]
        # Court action used on Task Board endpoint
        token = make_jws_token(
            private_key,
            platform_agent_id,
            {
                "action": "file_dispute",
                "task_id": task_id,
                "claimant_id": "a-xxx",
                "respondent_id": "a-xxx",
                "claim": "Test claim",
                "escrow_id": "esc-xxx",
            },
        )

        resp = await client.post(
            f"/tasks/{task_id}/ruling",
            json={"token": token},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_reputation_token_rejected(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """REPLAY-03: Reputation submit_feedback token rejected on Task Board approve."""
        task_id = make_task_id()
        private_key = alice_keypair[0]
        # Reputation action used on Task Board endpoint
        token = make_jws_token(
            private_key,
            alice_agent_id,
            {
                "action": "submit_feedback",
                "task_id": task_id,
                "from_agent_id": alice_agent_id,
                "to_agent_id": "a-xxx",
                "category": "spec_quality",
                "rating": "satisfied",
            },
        )

        resp = await client.post(
            f"/tasks/{task_id}/approve",
            json={"token": token},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"


class TestAuthSecurity:
    """Category 6: Cross-cutting security assertions (SEC-AUTH-01 to SEC-AUTH-03)."""

    @pytest.mark.unit
    async def test_error_envelope_consistency(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """SEC-AUTH-01: All auth error responses have consistent error envelope structure."""
        task_id = make_task_id()
        private_key = alice_keypair[0]

        # Trigger INVALID_JWS
        resp_invalid_jws = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": None},
        )

        # Trigger INVALID_PAYLOAD (missing action)
        token_no_action = make_jws_token(
            private_key,
            alice_agent_id,
            {"task_id": task_id, "poster_id": alice_agent_id},
        )
        resp_invalid_payload = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": token_no_action},
        )

        # Trigger FORBIDDEN (tampered JWS) -- need a task first
        create_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert create_resp.status_code == 201
        real_task_id = create_resp.json()["task_id"]

        valid_token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": real_task_id},
        )
        tampered = tamper_jws(valid_token)
        resp_forbidden = await client.get(
            f"/tasks/{real_task_id}/bids",
            headers={"Authorization": f"Bearer {tampered}"},
        )

        # Trigger IDENTITY_SERVICE_UNAVAILABLE
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            side_effect=TimeoutError("Identity service timed out")
        )
        cancel_token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "cancel_task", "task_id": task_id, "poster_id": alice_agent_id},
        )
        resp_identity = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": cancel_token},
        )

        # Verify all error envelopes have the required structure
        for resp in [resp_invalid_jws, resp_invalid_payload, resp_forbidden, resp_identity]:
            data = resp.json()
            assert "error" in data, f"Missing 'error' field in response: {data}"
            assert isinstance(data["error"], str), f"'error' is not a string: {data}"
            assert "message" in data, f"Missing 'message' field in response: {data}"
            assert isinstance(data["message"], str), f"'message' is not a string: {data}"
            assert "details" in data, f"Missing 'details' field in response: {data}"
            assert isinstance(data["details"], dict), f"'details' is not an object: {data}"

    @pytest.mark.unit
    async def test_no_internal_error_leakage(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """SEC-AUTH-02: Auth error messages never leak internal details."""
        task_id = make_task_id()
        private_key = alice_keypair[0]

        # Collect error responses from multiple auth failure scenarios
        error_responses = []

        # INVALID_JWS
        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": None},
        )
        error_responses.append(resp)

        # FORBIDDEN (tampered JWS on Bearer endpoint) -- need a task first
        create_resp = await create_task(client, alice_keypair, alice_agent_id)
        assert create_resp.status_code == 201
        real_task_id = create_resp.json()["task_id"]

        valid_token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "list_bids", "task_id": real_task_id},
        )
        tampered = tamper_jws(valid_token)
        resp = await client.get(
            f"/tasks/{real_task_id}/bids",
            headers={"Authorization": f"Bearer {tampered}"},
        )
        error_responses.append(resp)

        # IDENTITY_SERVICE_UNAVAILABLE
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            side_effect=TimeoutError("Identity service timed out")
        )
        cancel_token = make_jws_token(
            private_key,
            alice_agent_id,
            {"action": "cancel_task", "task_id": task_id, "poster_id": alice_agent_id},
        )
        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": cancel_token},
        )
        error_responses.append(resp)

        # Verify none of the error messages leak internal details
        for error_resp in error_responses:
            data = error_resp.json()
            message = data.get("message", "")
            details_str = str(data.get("details", {}))
            combined = f"{message} {details_str}"

            for pattern in _LEAK_PATTERNS:
                assert not pattern.search(combined), (
                    f"Internal detail leaked in error response: "
                    f"pattern={pattern.pattern!r}, response={data}"
                )

    @pytest.mark.unit
    async def test_cross_service_token_reuse_rejected(
        self,
        client: AsyncClient,
        platform_keypair: tuple[Ed25519PrivateKey, str],
        platform_agent_id: str,
    ) -> None:
        """SEC-AUTH-03: JWS token with cross-service action rejected on task creation."""
        private_key = platform_keypair[0]
        # Central Bank "create_account" action used on Task Board
        cross_service_token = make_jws_token(
            private_key,
            platform_agent_id,
            {"action": "create_account", "agent_id": platform_agent_id},
        )

        resp = await client.post(
            "/tasks",
            json={
                "task_token": cross_service_token,
                "escrow_token": cross_service_token,
            },
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_PAYLOAD"

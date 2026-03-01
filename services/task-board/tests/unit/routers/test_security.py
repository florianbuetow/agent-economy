"""HTTP method, error precedence, and cross-cutting security tests for Task Board service."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from tests.helpers import make_fake_jws, make_jws_token, tamper_jws
from tests.unit.routers.conftest import (
    accept_bid,
    create_task,
    make_task_id,
    setup_task_in_execution,
    setup_task_in_review,
    submit_bid,
    upload_asset,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient

UUID4_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class TestHTTPMethods:
    """HTTP-01: Wrong HTTP methods return 405."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "method,path",
        [
            ("DELETE", "/tasks"),
            ("PATCH", "/tasks"),
            ("DELETE", "/tasks/t-fake-id"),
            ("PATCH", "/tasks/t-fake-id"),
            ("GET", "/tasks/t-fake-id/cancel"),
            ("DELETE", "/tasks/t-fake-id/bids"),
            ("GET", "/tasks/t-fake-id/bids/bid-fake/accept"),
            ("DELETE", "/tasks/t-fake-id/assets"),
            ("GET", "/tasks/t-fake-id/submit"),
            ("GET", "/tasks/t-fake-id/approve"),
            ("GET", "/tasks/t-fake-id/dispute"),
            ("GET", "/tasks/t-fake-id/ruling"),
            ("POST", "/health"),
        ],
    )
    async def test_wrong_http_methods(
        self,
        client: AsyncClient,
        method: str,
        path: str,
    ) -> None:
        """HTTP-01: Wrong HTTP methods return 405."""
        resp = await client.request(method, path)
        assert resp.status_code == 405


class TestErrorPrecedence:
    """PREC-01 to PREC-10: Error precedence chain tests."""

    @pytest.mark.unit
    async def test_content_type_before_token(
        self,
        client: AsyncClient,
    ) -> None:
        """PREC-01: Content-Type before token -- text/plain returns 415."""
        resp = await client.post(
            "/tasks",
            content=b'{"task_token": "invalid"}',
            headers={"Content-Type": "text/plain"},
        )
        assert resp.status_code == 415
        assert resp.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"

    @pytest.mark.unit
    async def test_body_size_before_token(
        self,
        client: AsyncClient,
    ) -> None:
        """PREC-02: Body size before token -- oversized body returns 413."""
        # max_body_size is 1048576 (1 MB) in conftest config
        oversized_body = b"x" * (1048576 + 1)
        resp = await client.post(
            "/tasks",
            content=oversized_body,
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413
        assert resp.json()["error"] == "PAYLOAD_TOO_LARGE"

    @pytest.mark.unit
    async def test_json_parsing_before_token(
        self,
        client: AsyncClient,
    ) -> None:
        """PREC-03: JSON parsing before token -- malformed JSON returns 400."""
        resp = await client.post(
            "/tasks",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JSON"

    @pytest.mark.unit
    async def test_token_format_before_payload(
        self,
        client: AsyncClient,
    ) -> None:
        """PREC-04: Token format before payload -- malformed token returns 400."""
        task_id = make_task_id()
        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": 12345},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "INVALID_JWS"

    @pytest.mark.unit
    @pytest.mark.usefixtures("mock_identity_unavailable")
    async def test_identity_service_before_payload(
        self,
        client: AsyncClient,
        alice_agent_id: str,
    ) -> None:
        """PREC-05: Identity service before payload -- unavailable returns 502."""
        task_id = make_task_id()
        token = make_fake_jws(
            {"action": "wrong_action", "task_id": task_id},
            kid=alice_agent_id,
        )
        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": token},
        )
        assert resp.status_code == 502
        assert resp.json()["error"] == "IDENTITY_SERVICE_UNAVAILABLE"

    @pytest.mark.unit
    async def test_signature_validity_before_payload_content(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """PREC-06: Signature before payload -- tampered JWS returns 403."""
        task_id = make_task_id()
        valid_token = make_jws_token(
            alice_keypair[0],
            alice_agent_id,
            {
                "action": "wrong_action",
                "task_id": task_id,
                "poster_id": alice_agent_id,
            },
        )
        tampered_token = tamper_jws(valid_token)
        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": tampered_token},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_action_validation_before_signer_matching(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """PREC-07: Action before signer -- wrong action returns 400."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        token = make_jws_token(
            bob_keypair[0],
            bob_agent_id,
            {
                "action": "submit_bid",
                "task_id": task_id,
                "poster_id": alice_agent_id,
            },
        )
        cancel_resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": token},
        )
        assert cancel_resp.status_code == 400
        assert cancel_resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    async def test_task_existence_before_signer_matching(
        self,
        client: AsyncClient,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """PREC-08: Task existence before signer -- returns 404."""
        nonexistent_id = "t-00000000-0000-0000-0000-999999999999"
        token = make_jws_token(
            bob_keypair[0],
            bob_agent_id,
            {
                "action": "cancel_task",
                "task_id": nonexistent_id,
                "poster_id": bob_agent_id,
            },
        )
        resp = await client.post(
            f"/tasks/{nonexistent_id}/cancel",
            json={"token": token},
        )
        assert resp.status_code == 404
        assert resp.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_status_validation_before_domain_validation(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """PREC-09: Status before domain -- wrong status returns 409."""
        task_id = await setup_task_in_review(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        approve_token = make_jws_token(
            alice_keypair[0],
            alice_agent_id,
            {
                "action": "approve_task",
                "task_id": task_id,
                "poster_id": alice_agent_id,
            },
        )
        approve_resp = await client.post(
            f"/tasks/{task_id}/approve",
            json={"token": approve_token},
        )
        assert approve_resp.status_code == 200

        dispute_token = make_jws_token(
            alice_keypair[0],
            alice_agent_id,
            {
                "action": "file_dispute",
                "task_id": task_id,
                "poster_id": alice_agent_id,
                "reason": "",
            },
        )
        resp = await client.post(
            f"/tasks/{task_id}/dispute",
            json={"token": dispute_token},
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    @pytest.mark.usefixtures("mock_central_bank_unavailable")
    async def test_token_mismatch_before_central_bank_errors(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """PREC-10: Token mismatch before Central Bank -- returns 400."""
        task_id_1 = make_task_id()
        task_id_2 = make_task_id()

        task_payload = {
            "action": "create_task",
            "task_id": task_id_1,
            "poster_id": alice_agent_id,
            "title": "Test task",
            "spec": "Test specification",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "execution_deadline_seconds": 86400,
            "review_deadline_seconds": 86400,
        }
        task_token = make_jws_token(alice_keypair[0], alice_agent_id, task_payload)

        escrow_payload = {
            "action": "escrow_lock",
            "task_id": task_id_2,
            "agent_id": alice_agent_id,
            "amount": 100,
        }
        escrow_token = make_jws_token(alice_keypair[0], alice_agent_id, escrow_payload)

        resp = await client.post(
            "/tasks",
            json={
                "task_token": task_token,
                "escrow_token": escrow_token,
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "TOKEN_MISMATCH"


class TestCrossCuttingSecurity:
    """SEC-01 to SEC-09: Cross-cutting security assertion tests."""

    @pytest.mark.unit
    async def test_error_envelope_consistency(
        self,
        client: AsyncClient,
    ) -> None:
        """SEC-01: All error responses have consistent envelope."""
        error_responses = []

        # Trigger INVALID_JSON
        resp = await client.post(
            "/tasks",
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        error_responses.append(resp)

        # Trigger INVALID_JWS
        resp = await client.post(
            "/tasks",
            json={
                "task_token": "not-a-jws",
                "escrow_token": "also-bad",
            },
        )
        error_responses.append(resp)

        # Trigger TASK_NOT_FOUND
        nonexistent = "t-00000000-0000-0000-0000-000000000000"
        resp = await client.get(f"/tasks/{nonexistent}")
        error_responses.append(resp)

        # Trigger METHOD_NOT_ALLOWED
        resp = await client.delete("/tasks")
        error_responses.append(resp)

        # Trigger UNSUPPORTED_MEDIA_TYPE
        resp = await client.post(
            "/tasks",
            content=b"plain text",
            headers={"Content-Type": "text/plain"},
        )
        error_responses.append(resp)

        assert len(error_responses) >= 5
        for error_resp in error_responses:
            data = error_resp.json()
            assert "error" in data, f"Missing 'error' in {data}"
            assert isinstance(data["error"], str)
            assert "message" in data, f"Missing 'message' in {data}"
            assert isinstance(data["message"], str)
            assert "details" in data, f"Missing 'details' in {data}"
            assert isinstance(data["details"], dict)

    @pytest.mark.unit
    async def test_no_internal_error_leakage(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """SEC-02: Error messages never leak internal details."""
        leak_patterns = [
            "traceback",
            "Traceback",
            'File "',
            "File '",
            "sqlite",
            "SELECT ",
            "INSERT ",
            "UPDATE ",
            "DELETE FROM",
            ".py",
            "line ",
            "Exception",
            "raise ",
            "localhost:8001",
            "localhost:8002",
            "private_key",
        ]

        error_responses = []

        # Trigger INVALID_JSON
        resp = await client.post(
            "/tasks",
            content=b"{bad json",
            headers={"Content-Type": "application/json"},
        )
        error_responses.append(resp)

        # Trigger TASK_NOT_FOUND
        nonexistent = "t-00000000-0000-0000-0000-000000000000"
        resp = await client.get(f"/tasks/{nonexistent}")
        error_responses.append(resp)

        # Trigger FORBIDDEN via tampered JWS
        task_id = make_task_id()
        valid_token = make_jws_token(
            alice_keypair[0],
            alice_agent_id,
            {
                "action": "cancel_task",
                "task_id": task_id,
                "poster_id": alice_agent_id,
            },
        )
        tampered_token = tamper_jws(valid_token)
        resp = await client.post(
            f"/tasks/{task_id}/cancel",
            json={"token": tampered_token},
        )
        error_responses.append(resp)

        for error_resp in error_responses:
            data = error_resp.json()
            message = data.get("message", "")
            details_str = str(data.get("details", {}))
            combined = message + details_str
            for pattern in leak_patterns:
                assert pattern not in combined, f"Internal leak: '{pattern}' in {data}"

    @pytest.mark.unit
    async def test_task_ids_match_format(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """SEC-03: Task IDs match t-<uuid4> format."""
        for _ in range(5):
            resp = await create_task(client, alice_keypair, alice_agent_id)
            assert resp.status_code == 201
            task_id = resp.json()["task_id"]
            assert task_id.startswith("t-")
            uuid_part = task_id[2:]
            assert UUID4_PATTERN.match(uuid_part), f"task_id invalid: {task_id}"

    @pytest.mark.unit
    async def test_bid_ids_match_format(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """SEC-04: Bid IDs match bid-<uuid4> format."""
        bid_ids = []
        for i in range(5):
            resp = await create_task(client, alice_keypair, alice_agent_id)
            assert resp.status_code == 201
            task_id = resp.json()["task_id"]

            bid_resp = await submit_bid(
                client,
                bob_keypair,
                bob_agent_id,
                task_id,
                amount=90 + i,
            )
            assert bid_resp.status_code == 201
            bid_ids.append(bid_resp.json()["bid_id"])

        for bid_id in bid_ids:
            assert bid_id.startswith("bid-")
            uuid_part = bid_id[4:]
            assert UUID4_PATTERN.match(uuid_part), f"bid_id invalid: {bid_id}"

    @pytest.mark.unit
    async def test_asset_ids_match_format(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """SEC-05: Asset IDs match asset-<uuid4> format."""
        task_id, _bid_id = await setup_task_in_execution(
            client,
            alice_keypair,
            alice_agent_id,
            bob_keypair,
            bob_agent_id,
        )

        asset_ids = []
        for i in range(5):
            resp = await upload_asset(
                client,
                bob_keypair,
                bob_agent_id,
                task_id,
                filename=f"file-{i}.txt",
                content=f"content {i}".encode(),
            )
            assert resp.status_code == 201
            asset_ids.append(resp.json()["asset_id"])

        for asset_id in asset_ids:
            assert asset_id.startswith("asset-")
            uuid_part = asset_id[6:]
            assert UUID4_PATTERN.match(uuid_part), f"asset_id invalid: {asset_id}"

    @pytest.mark.unit
    async def test_escrow_ids_match_format(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
    ) -> None:
        """SEC-06: Escrow IDs match esc-<uuid4> format."""
        for _ in range(5):
            resp = await create_task(client, alice_keypair, alice_agent_id)
            assert resp.status_code == 201
            escrow_id = resp.json()["escrow_id"]
            assert escrow_id.startswith("esc-")
            uuid_part = escrow_id[4:]
            assert UUID4_PATTERN.match(uuid_part), f"escrow_id invalid: {escrow_id}"

    @pytest.mark.unit
    async def test_cross_action_token_replay_rejected(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """SEC-07: Cross-action token replay rejected.

        Bid token replayed on submit endpoint returns 400 INVALID_PAYLOAD.
        """
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        bid_payload = {
            "action": "submit_bid",
            "task_id": task_id,
            "bidder_id": bob_agent_id,
            "amount": 90,
        }
        bid_token = make_jws_token(bob_keypair[0], bob_agent_id, bid_payload)

        bid_resp = await client.post(
            f"/tasks/{task_id}/bids",
            json={"token": bid_token},
        )
        assert bid_resp.status_code == 201
        bid_id = bid_resp.json()["bid_id"]

        accept_resp = await accept_bid(client, alice_keypair, alice_agent_id, task_id, bid_id)
        assert accept_resp.status_code == 200

        replay_resp = await client.post(
            f"/tasks/{task_id}/submit",
            json={"token": bid_token},
        )
        assert replay_resp.status_code == 400
        assert replay_resp.json()["error"] == "INVALID_PAYLOAD"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "sqli_path",
        [
            "/tasks/' OR '1'='1",
            "/tasks/' OR '1'='1/bids",
            "/tasks/' OR '1'='1/assets",
        ],
    )
    async def test_sql_injection_in_path_params(
        self,
        client: AsyncClient,
        sqli_path: str,
    ) -> None:
        """SEC-08: SQL injection in path params returns 404, no leakage."""
        resp = await client.get(sqli_path)
        assert resp.status_code == 404

        body_text = resp.text
        for pattern in [
            "SELECT",
            "INSERT",
            "DROP",
            "sqlite",
            "Traceback",
            'File "',
        ]:
            assert pattern not in body_text, f"Leak '{pattern}' in SQL injection response"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "traversal_path",
        [
            "../../etc/passwd",
            "../../../config.yaml",
        ],
    )
    async def test_path_traversal_in_asset_download(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        traversal_path: str,
    ) -> None:
        """SEC-09: Path traversal in asset download returns 404."""
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        download_resp = await client.get(f"/tasks/{task_id}/assets/{traversal_path}")
        assert download_resp.status_code == 404
        body_text = download_resp.text
        assert "root:" not in body_text, "passwd file content leaked"
        assert "database:" not in body_text, "config content leaked"

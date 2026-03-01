"""Asset upload and retrieval endpoint tests for Task Board service."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    create_task,
    setup_task_in_execution,
    upload_asset,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient


NONEXISTENT_TASK_ID = "t-00000000-0000-0000-0000-000000000000"
NONEXISTENT_ASSET_ID = "asset-00000000-0000-0000-0000-000000000000"


class TestAssetUpload:
    """Tests for POST /tasks/{task_id}/assets."""

    @pytest.mark.unit
    async def test_worker_uploads_file(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-01: Worker uploads a file via multipart, receives 201 with asset metadata."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        content = b"test file content for upload"
        resp = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="login-page.zip",
            content=content,
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "asset_id" in data
        assert data["asset_id"].startswith("asset-")
        assert data["task_id"] == task_id
        assert data["uploader_id"] == bob_agent_id
        assert data["filename"] == "login-page.zip"
        assert "size_bytes" in data
        assert data["size_bytes"] == len(content)
        assert "uploaded_at" in data
        # Verify uploaded_at is valid ISO 8601
        datetime.fromisoformat(data["uploaded_at"])

    @pytest.mark.unit
    async def test_upload_nonexistent_task(
        self,
        client: AsyncClient,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-02: Upload to a nonexistent task returns 404 TASK_NOT_FOUND."""
        resp = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            NONEXISTENT_TASK_ID,
        )

        assert resp.status_code == 404
        assert resp.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_upload_wrong_status_task(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-03: Upload to a task not in ACCEPTED/EXECUTION status returns 409 INVALID_STATUS."""
        # Create a task but do NOT accept any bid — task stays in OPEN status
        resp = await create_task(client, alice_keypair, alice_agent_id)
        assert resp.status_code == 201
        task_id = resp.json()["task_id"]

        upload_resp = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
        )

        assert upload_resp.status_code == 409
        assert upload_resp.json()["error"] == "INVALID_STATUS"

    @pytest.mark.unit
    async def test_non_worker_cannot_upload(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
        carol_keypair: tuple[Ed25519PrivateKey, str],
        carol_agent_id: str,
    ) -> None:
        """AU-04: Non-worker (Carol) attempting to upload returns 403 FORBIDDEN."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Carol is not the worker — should be forbidden
        resp = await upload_asset(
            client,
            carol_keypair,
            carol_agent_id,
            task_id,
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"

    @pytest.mark.unit
    async def test_no_file_in_multipart(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-05: POST with Authorization header but no file part returns 400 NO_FILE."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        private_key = bob_keypair[0]
        payload = {
            "action": "upload_asset",
            "task_id": task_id,
        }
        token = make_jws_token(private_key, bob_agent_id, payload)

        # POST without any file part
        resp = await client.post(
            f"/tasks/{task_id}/assets",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "NO_FILE"

    @pytest.mark.unit
    async def test_file_exceeds_max_size(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-06: Uploading a file exceeding max_file_size returns 413 FILE_TOO_LARGE."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # max_file_size is configured to 10485760 (10 MB) in conftest
        large_content = b"x" * (10 * 1024 * 1024 + 1)
        resp = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="large-file.bin",
            content=large_content,
        )

        assert resp.status_code == 413
        assert resp.json()["error"] == "FILE_TOO_LARGE"

    @pytest.mark.unit
    async def test_multiple_uploads_succeed(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-07: Multiple file uploads all return 201 with different asset_ids."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp1 = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="file1.txt",
            content=b"content one",
        )
        resp2 = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="file2.txt",
            content=b"content two",
        )

        assert resp1.status_code == 201
        assert resp2.status_code == 201

        asset_id_1 = resp1.json()["asset_id"]
        asset_id_2 = resp2.json()["asset_id"]
        assert asset_id_1 != asset_id_2

    @pytest.mark.unit
    async def test_too_many_assets(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-08: Uploading more than max_assets_per_task (20) returns 409 TOO_MANY_ASSETS."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Upload exactly 20 files (the maximum)
        for i in range(20):
            resp = await upload_asset(
                client,
                bob_keypair,
                bob_agent_id,
                task_id,
                filename=f"file-{i}.txt",
                content=f"content {i}".encode(),
            )
            assert resp.status_code == 201

        # The 21st upload should fail
        resp = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="file-overflow.txt",
            content=b"one too many",
        )

        assert resp.status_code == 409
        assert resp.json()["error"] == "TOO_MANY_ASSETS"

    @pytest.mark.unit
    async def test_content_hash_is_sha256(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-09: The content_hash in the response is the SHA-256 hex digest of the file."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        content = b"deterministic content for hash verification"
        expected_hash = hashlib.sha256(content).hexdigest()

        resp = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="hashable.txt",
            content=content,
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "content_hash" in data
        assert data["content_hash"] == expected_hash

    @pytest.mark.unit
    async def test_upload_uses_bearer_token(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-10: Asset upload authenticates via Bearer token in Authorization header."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        private_key = bob_keypair[0]
        payload = {
            "action": "upload_asset",
            "task_id": task_id,
        }
        token = make_jws_token(private_key, bob_agent_id, payload)

        # Verify the token is a three-part JWS compact serialization
        parts = token.split(".")
        assert len(parts) == 3

        # Upload with explicit Bearer header format and verify success
        resp = await client.post(
            f"/tasks/{task_id}/assets",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("bearer-test.txt", b"bearer content", "application/octet-stream")},
        )

        assert resp.status_code == 201

    @pytest.mark.unit
    async def test_poster_cannot_upload(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AU-11: Poster (Alice) attempting to upload returns 403 FORBIDDEN."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        # Alice is the poster — should be forbidden
        resp = await upload_asset(
            client,
            alice_keypair,
            alice_agent_id,
            task_id,
        )

        assert resp.status_code == 403
        assert resp.json()["error"] == "FORBIDDEN"


class TestAssetRetrieval:
    """Tests for GET /tasks/{task_id}/assets and GET /tasks/{task_id}/assets/{asset_id}."""

    @pytest.mark.unit
    async def test_list_assets(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AR-01: GET /tasks/{task_id}/assets returns 200 with array of uploaded assets."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="file1.txt",
            content=b"content one",
        )
        await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="file2.txt",
            content=b"content two",
        )

        resp = await client.get(f"/tasks/{task_id}/assets")

        assert resp.status_code == 200
        data = resp.json()
        assert "assets" in data
        assert len(data["assets"]) == 2

        for asset in data["assets"]:
            assert "asset_id" in asset
            assert "uploader_id" in asset
            assert "filename" in asset
            assert "size_bytes" in asset
            assert "uploaded_at" in asset
            assert asset["uploader_id"] == bob_agent_id

    @pytest.mark.unit
    async def test_list_assets_empty(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AR-02: GET /tasks/{task_id}/assets with no uploads returns 200 with empty array."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await client.get(f"/tasks/{task_id}/assets")

        assert resp.status_code == 200
        data = resp.json()
        assert "assets" in data
        assert data["assets"] == []

    @pytest.mark.unit
    async def test_download_asset(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AR-03: GET /tasks/{task_id}/assets/{asset_id} returns 200 with binary content."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        file_content = b"test content for download"
        upload_resp = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="login-page.zip",
            content=file_content,
        )
        assert upload_resp.status_code == 201
        asset_id = upload_resp.json()["asset_id"]

        resp = await client.get(f"/tasks/{task_id}/assets/{asset_id}")

        assert resp.status_code == 200
        assert resp.content == file_content

    @pytest.mark.unit
    async def test_download_nonexistent_asset(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AR-04: GET /tasks/{task_id}/assets/{asset_id} for nonexistent asset returns 404."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        resp = await client.get(f"/tasks/{task_id}/assets/{NONEXISTENT_ASSET_ID}")

        assert resp.status_code == 404
        assert resp.json()["error"] == "ASSET_NOT_FOUND"

    @pytest.mark.unit
    async def test_list_assets_nonexistent_task(
        self,
        client: AsyncClient,
    ) -> None:
        """AR-05: GET /tasks/{task_id}/assets for nonexistent task returns 404 TASK_NOT_FOUND."""
        resp = await client.get(f"/tasks/{NONEXISTENT_TASK_ID}/assets")

        assert resp.status_code == 404
        assert resp.json()["error"] == "TASK_NOT_FOUND"

    @pytest.mark.unit
    async def test_assets_are_public_no_auth(
        self,
        client: AsyncClient,
        alice_keypair: tuple[Ed25519PrivateKey, str],
        alice_agent_id: str,
        bob_keypair: tuple[Ed25519PrivateKey, str],
        bob_agent_id: str,
    ) -> None:
        """AR-06: Asset list and download endpoints require no authentication."""
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        upload_resp = await upload_asset(
            client,
            bob_keypair,
            bob_agent_id,
            task_id,
            filename="public-file.txt",
            content=b"public content",
        )
        assert upload_resp.status_code == 201
        asset_id = upload_resp.json()["asset_id"]

        # List assets without any Authorization header
        list_resp = await client.get(f"/tasks/{task_id}/assets")
        assert list_resp.status_code == 200

        # Download asset without any Authorization header
        download_resp = await client.get(f"/tasks/{task_id}/assets/{asset_id}")
        assert download_resp.status_code == 200
        assert download_resp.content == b"public content"

"""Asset read authorization, safe download headers, storage quota and upload idempotency."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from service_auth.jws import TokenPolicy

import task_board_service.core.asset_upload as asset_upload_module
from task_board_service.config import AssetQuotaConfig, Settings, get_settings
from task_board_service.core.state import get_app_state
from task_board_service.operator_authority import OperatorAssetAuthority
from tests.helpers import make_jws_token
from tests.unit.routers.conftest import (
    approve_task,
    setup_task_in_execution,
    submit_deliverable,
    upload_asset,
)

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from httpx import AsyncClient, Response

pytestmark = pytest.mark.unit

_Keypair = tuple["Ed25519PrivateKey", str]


def _list_headers(keypair: _Keypair, agent_id: str, task_id: str) -> dict[str, str]:
    token = make_jws_token(
        keypair[0],
        agent_id,
        {"action": "list_assets", "task_id": task_id},
        resource=f"GET /tasks/{task_id}/assets",
    )
    return {"Authorization": f"Bearer {token}"}


def _download_headers(
    keypair: _Keypair, agent_id: str, task_id: str, asset_id: str
) -> dict[str, str]:
    token = make_jws_token(
        keypair[0],
        agent_id,
        {"action": "download_asset", "task_id": task_id, "asset_id": asset_id},
        resource=f"GET /tasks/{task_id}/assets/{asset_id}",
    )
    return {"Authorization": f"Bearer {token}"}


async def _read_both(
    client: AsyncClient,
    keypair: _Keypair,
    agent_id: str,
    task_id: str,
    asset_id: str,
) -> tuple[Response, Response]:
    listing = await client.get(
        f"/tasks/{task_id}/assets", headers=_list_headers(keypair, agent_id, task_id)
    )
    download = await client.get(
        f"/tasks/{task_id}/assets/{asset_id}",
        headers=_download_headers(keypair, agent_id, task_id, asset_id),
    )
    return listing, download


async def _submitted_task_with_asset(
    client: AsyncClient,
    poster: tuple[_Keypair, str],
    worker: tuple[_Keypair, str],
    content: bytes,
) -> tuple[str, str]:
    task_id, _bid_id = await setup_task_in_execution(
        client, poster[0], poster[1], worker[0], worker[1]
    )
    uploaded = await upload_asset(
        client, worker[0], worker[1], task_id, filename="answer.txt", content=content
    )
    assert uploaded.status_code == 201
    submitted = await submit_deliverable(client, worker[0], worker[1], task_id)
    assert submitted.status_code == 200
    return task_id, str(uploaded.json()["asset_id"])


def _configure_operator(operator: _Keypair, operator_id: str) -> None:
    state = get_app_state()
    assert state.token_validator is not None
    assert state.identity_client is not None
    state.token_validator.configure_operator_asset_authority(
        OperatorAssetAuthority(
            public_key=operator[0].public_key(),
            canonical_public_key=operator[1],
            token_policy=TokenPolicy(max_lifetime_seconds=60, clock_skew_seconds=0),
        )
    )
    state.identity_client.get_agent = AsyncMock(  # type: ignore[method-assign]
        side_effect=lambda agent_id: {
            "agent_id": agent_id,
            "public_key": operator[1] if agent_id == operator_id else "ed25519:other",
        }
    )


class TestAssetReadAuthorization:
    """Plan 0.6: list/download require a signed token from a task principal."""

    async def test_unsigned_reads_of_submitted_deliverable_are_rejected(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
    ) -> None:
        task_id, asset_id = await _submitted_task_with_asset(
            client, (alice_keypair, alice_agent_id), (bob_keypair, bob_agent_id), b"work"
        )

        listing = await client.get(f"/tasks/{task_id}/assets")
        download = await client.get(f"/tasks/{task_id}/assets/{asset_id}")

        assert (listing.status_code, listing.json()["error"]) == (401, "unauthorized")
        assert (download.status_code, download.json()["error"]) == (401, "unauthorized")

    async def test_third_party_reads_of_submitted_deliverable_are_forbidden(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
        carol_keypair: _Keypair,
        carol_agent_id: str,
    ) -> None:
        task_id, asset_id = await _submitted_task_with_asset(
            client, (alice_keypair, alice_agent_id), (bob_keypair, bob_agent_id), b"work"
        )

        listing, download = await _read_both(
            client, carol_keypair, carol_agent_id, task_id, asset_id
        )

        # Owner rule: a nonterminal job's poster inputs are public to signed agents, so a
        # third party may list, but sees no worker output and cannot download it.
        assert listing.status_code == 200
        assert all(item["role"] == "input" for item in listing.json()["assets"])
        assert (download.status_code, download.json()["error"]) == (403, "forbidden")

    @pytest.mark.parametrize("reader", ["poster", "worker"])
    async def test_task_parties_read_submitted_deliverable(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
        reader: str,
    ) -> None:
        task_id, asset_id = await _submitted_task_with_asset(
            client, (alice_keypair, alice_agent_id), (bob_keypair, bob_agent_id), b"work"
        )
        keypair, agent_id = (
            (alice_keypair, alice_agent_id) if reader == "poster" else (bob_keypair, bob_agent_id)
        )

        listing, download = await _read_both(client, keypair, agent_id, task_id, asset_id)

        assert listing.status_code == 200
        assert [item["asset_id"] for item in listing.json()["assets"]] == [asset_id]
        assert download.status_code == 200 and download.content == b"work"

    async def test_platform_reads_submitted_deliverable(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
        platform_keypair: _Keypair,
        platform_agent_id: str,
    ) -> None:
        task_id, asset_id = await _submitted_task_with_asset(
            client, (alice_keypair, alice_agent_id), (bob_keypair, bob_agent_id), b"work"
        )

        listing, download = await _read_both(
            client, platform_keypair, platform_agent_id, task_id, asset_id
        )

        assert listing.status_code == 200
        assert download.status_code == 200 and download.content == b"work"

    async def test_operator_reads_submitted_deliverable(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
        carol_keypair: _Keypair,
        carol_agent_id: str,
    ) -> None:
        task_id, asset_id = await _submitted_task_with_asset(
            client, (alice_keypair, alice_agent_id), (bob_keypair, bob_agent_id), b"work"
        )
        _configure_operator(carol_keypair, carol_agent_id)

        listing, download = await _read_both(
            client, carol_keypair, carol_agent_id, task_id, asset_id
        )

        assert listing.status_code == 200
        assert download.status_code == 200 and download.content == b"work"

    async def test_parties_keep_signed_access_after_approval_and_strangers_never(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
        carol_keypair: _Keypair,
        carol_agent_id: str,
    ) -> None:
        task_id, asset_id = await _submitted_task_with_asset(
            client, (alice_keypair, alice_agent_id), (bob_keypair, bob_agent_id), b"work"
        )
        approved = await approve_task(client, alice_keypair, alice_agent_id, task_id)
        assert approved.status_code in {200, 202}

        _poster_list, poster_download = await _read_both(
            client, alice_keypair, alice_agent_id, task_id, asset_id
        )
        _worker_list, worker_download = await _read_both(
            client, bob_keypair, bob_agent_id, task_id, asset_id
        )
        _stranger_list, stranger_download = await _read_both(
            client, carol_keypair, carol_agent_id, task_id, asset_id
        )
        unsigned = await client.get(f"/tasks/{task_id}/assets/{asset_id}")

        assert poster_download.status_code == 200 and poster_download.content == b"work"
        assert worker_download.status_code == 200 and worker_download.content == b"work"
        assert stranger_download.status_code == 403
        assert unsigned.status_code == 401

    async def test_list_token_is_bound_to_the_task(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
    ) -> None:
        task_id, _asset_id = await _submitted_task_with_asset(
            client, (alice_keypair, alice_agent_id), (bob_keypair, bob_agent_id), b"work"
        )
        other_task = "t-00000000-0000-4000-8000-0000000000aa"
        token = make_jws_token(
            alice_keypair[0],
            alice_agent_id,
            {"action": "list_assets", "task_id": other_task},
            resource=f"GET /tasks/{task_id}/assets",
        )

        response = await client.get(
            f"/tasks/{task_id}/assets", headers={"Authorization": f"Bearer {token}"}
        )

        assert (response.status_code, response.json()["error"]) == (400, "invalid_payload")


class TestSafeDownloadHeaders:
    """Plan L5: bytes are always served as an opaque attachment."""

    async def test_html_upload_downloads_as_octet_stream_attachment(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
    ) -> None:
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        token = make_jws_token(
            bob_keypair[0], bob_agent_id, {"action": "upload_asset", "task_id": task_id}
        )
        uploaded = await client.post(
            f"/tasks/{task_id}/assets",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("page.html", b"<script>alert(1)</script>", "text/html")},
        )
        assert uploaded.status_code == 201
        asset_id = uploaded.json()["asset_id"]

        listing, download = await _read_both(
            client, alice_keypair, alice_agent_id, task_id, asset_id
        )

        assert download.status_code == 200
        assert download.headers["content-type"] == "application/octet-stream"
        assert download.headers["content-disposition"].startswith("attachment;")
        assert download.headers["x-content-type-options"] == "nosniff"
        assert listing.json()["assets"][0]["content_type"] == "text/html"


def _patch_quota(monkeypatch: pytest.MonkeyPatch, *, per_agent: int, total: int) -> None:
    settings = get_settings().model_copy(
        update={
            "asset_quota": AssetQuotaConfig(
                max_total_bytes_per_agent=per_agent, max_total_bytes=total
            )
        }
    )
    monkeypatch.setattr(asset_upload_module, "get_settings", lambda: settings)


class TestStorageQuota:
    """Plan 2.6: uploads cannot exceed the configured per-agent or global total."""

    async def test_agent_over_its_quota_is_rejected(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
    ) -> None:
        _patch_quota(monkeypatch, per_agent=30, total=1000)
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        first = await upload_asset(client, bob_keypair, bob_agent_id, task_id, content=b"a" * 20)
        second = await upload_asset(client, bob_keypair, bob_agent_id, task_id, content=b"b" * 20)
        exact_fit = await upload_asset(
            client, bob_keypair, bob_agent_id, task_id, content=b"c" * 10
        )
        full = await upload_asset(client, bob_keypair, bob_agent_id, task_id, content=b"d")

        assert first.status_code == 201
        assert (second.status_code, second.json()["error"]) == (413, "storage_quota_exceeded")
        assert exact_fit.status_code == 201
        assert (full.status_code, full.json()["error"]) == (413, "storage_quota_exceeded")

    async def test_global_quota_applies_across_agents(
        self,
        client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
        carol_keypair: _Keypair,
        carol_agent_id: str,
    ) -> None:
        _patch_quota(monkeypatch, per_agent=1000, total=30)
        bob_task, _ = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )
        carol_task, _ = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, carol_keypair, carol_agent_id
        )

        bob_upload = await upload_asset(
            client, bob_keypair, bob_agent_id, bob_task, content=b"a" * 20
        )
        carol_upload = await upload_asset(
            client, carol_keypair, carol_agent_id, carol_task, content=b"b" * 20
        )

        assert bob_upload.status_code == 201
        assert (carol_upload.status_code, carol_upload.json()["error"]) == (
            413,
            "storage_quota_exceeded",
        )


async def _keyed_upload(
    client: AsyncClient,
    keypair: _Keypair,
    agent_id: str,
    task_id: str,
    key: str,
    content: bytes,
) -> Response:
    token = make_jws_token(keypair[0], agent_id, {"action": "upload_asset", "task_id": task_id})
    return await client.post(
        f"/tasks/{task_id}/assets",
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": key},
        files={"file": ("answer.txt", content, "text/plain")},
    )


class TestUploadIdempotency:
    """G04: a retried upload with the same key returns the stored asset."""

    async def test_retry_with_same_key_returns_same_asset(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
    ) -> None:
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        first = await _keyed_upload(client, bob_keypair, bob_agent_id, task_id, "k-1", b"x")
        retry = await _keyed_upload(client, bob_keypair, bob_agent_id, task_id, "k-1", b"x")
        other = await _keyed_upload(client, bob_keypair, bob_agent_id, task_id, "k-2", b"x")
        listing = await client.get(
            f"/tasks/{task_id}/assets",
            headers=_list_headers(bob_keypair, bob_agent_id, task_id),
        )

        assert first.status_code == 201
        assert retry.status_code == 201
        assert retry.json() == first.json()
        assert other.status_code == 201
        assert other.json()["asset_id"] != first.json()["asset_id"]
        assert len(listing.json()["assets"]) == 2

    @pytest.mark.parametrize("key", ["", "has space", "x" * 129, "a/b"])
    async def test_malformed_key_is_rejected(
        self,
        client: AsyncClient,
        alice_keypair: _Keypair,
        alice_agent_id: str,
        bob_keypair: _Keypair,
        bob_agent_id: str,
        key: str,
    ) -> None:
        task_id, _bid_id = await setup_task_in_execution(
            client, alice_keypair, alice_agent_id, bob_keypair, bob_agent_id
        )

        response = await _keyed_upload(client, bob_keypair, bob_agent_id, task_id, key, b"x")

        assert (response.status_code, response.json()["error"]) == (
            400,
            "invalid_idempotency_key",
        )


def test_asset_quota_section_is_required() -> None:
    with pytest.raises(ValidationError):
        AssetQuotaConfig.model_validate({"max_total_bytes_per_agent": 1})
    assert "asset_quota" in Settings.model_fields
    assert Settings.model_fields["asset_quota"].is_required()

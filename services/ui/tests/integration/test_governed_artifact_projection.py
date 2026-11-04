"""Current governed uploads appear in the human task drilldown."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from test_q_operator_participation_proxy import _install

pytestmark = pytest.mark.integration

if TYPE_CHECKING:
    from pathlib import Path


def _insert_governed_output(database: Path) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO board_artifact_manifests "
            "(manifest_id, task_id, kind, actor_id, format_version, state, "
            "canonical_manifest_json, manifest_sha256, frozen_at_us, "
            "publication_operation_id, public_task_id) "
            "VALUES (?, ?, 'submission', ?, 1, 'frozen', '{}', ?, ?, NULL, ?)",
            ("manifest-ui-output", "t-task1", "a-bob", "a" * 64, 1_000_000, "t-task1"),
        )
        upload_sql = (
            "INSERT INTO board_artifact_uploads "
            "(upload_operation_id, request_sha256, canonical_request_json, caller_kid, "
            "resource, task_id, manifest_id, actor_id, actor_role, display_filename, "
            "evidence_format, media_type, declared_size_bytes, content_sha256, "
            "expected_lifecycle_version, accepted_binding_kind, accepted_binding_sha256, "
            "manifest_kind, publication_operation_id, state, artifact_id, object_key, "
            "current_attempt_id, created_at_us, finalized_at_us) "
            "VALUES (?, ?, '{}', ?, 'POST /board/artifact-uploads/reserve', ?, ?, ?, "
            "'worker', ?, 'text', 'text/plain; charset=utf-8', ?, ?, 1, "
            "'publication_contract', ?, 'submission', NULL, ?, ?, ?, NULL, ?, ?)"
        )
        connection.executemany(
            upload_sql,
            [
                (
                    "upload-ui-output",
                    "b" * 64,
                    "gateway-task-board",
                    "t-task1",
                    "manifest-ui-output",
                    "a-bob",
                    "governed-answer.txt",
                    17,
                    "c" * 64,
                    "d" * 64,
                    "ready",
                    "artifact-ui-output",
                    "live/ui/output.blob",
                    2_000_000,
                    3_000_000,
                ),
                (
                    "upload-ui-archive",
                    "e" * 64,
                    "gateway-task-board",
                    "t-task1",
                    "manifest-ui-output",
                    "a-bob",
                    "archived-answer.txt",
                    19,
                    "f" * 64,
                    "1" * 64,
                    "archived",
                    "artifact-ui-archive",
                    "archive/ui/output.blob",
                    4_000_000,
                    5_000_000,
                ),
            ],
        )
        connection.commit()
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_task_drilldown_merges_ready_governed_output_once(
    client: Any,
    db_path: Path,
) -> None:
    _insert_governed_output(db_path)

    response = await client.get("/api/tasks/t-task1")

    assert response.status_code == 200
    assets = response.json()["assets"]
    governed = [asset for asset in assets if asset["asset_id"].startswith("asset-artifact-ui-")]
    assert governed == [
        {
            "asset_id": "asset-artifact-ui-output",
            "uploader_id": "a-bob",
            "filename": "governed-answer.txt",
            "content_type": "text/plain; charset=utf-8",
            "size_bytes": 17,
            "content_hash": "c" * 64,
            "uploaded_at": "1970-01-01T00:00:03Z",
            "role": "output",
        },
        {
            "asset_id": "asset-artifact-ui-archive",
            "uploader_id": "a-bob",
            "filename": "archived-answer.txt",
            "content_type": "text/plain; charset=utf-8",
            "size_bytes": 19,
            "content_hash": "f" * 64,
            "uploaded_at": "1970-01-01T00:00:05Z",
            "role": "output",
        },
    ]
    assert len({asset["asset_id"] for asset in assets}) == len(assets)
    assert all("storage_path" not in asset and "object_key" not in asset for asset in assets)


@pytest.mark.asyncio
async def test_projected_governed_ids_download_through_operator_proxy(
    client: Any,
    db_path: Path,
) -> None:
    _insert_governed_output(db_path)
    projected = (await client.get("/api/tasks/t-task1")).json()["assets"]
    governed = [asset for asset in projected if "ui-" in asset["asset_id"]]
    expected_ids = {"asset-artifact-ui-output", "asset-artifact-ui-archive"}

    fake = _install("a-bob")

    async def download(method: str, url: str, **_kwargs: Any) -> httpx.Response:
        request = httpx.Request(method, url)
        asset_id = url.rsplit("/", 1)[-1]
        if asset_id not in expected_ids:
            return httpx.Response(
                404,
                request=request,
                json={"error": "asset_not_found", "message": "Asset not found", "details": {}},
            )
        return httpx.Response(
            200,
            request=request,
            content=asset_id.encode(),
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Disposition": (
                    f"attachment; filename=\"{asset_id}.txt\"; filename*=UTF-8''{asset_id}.txt"
                ),
            },
        )

    fake._request_raw = download  # type: ignore[method-assign]
    responses = [
        await client.get(f"/api/proxy/tasks/t-task1/assets/{asset['asset_id']}")
        for asset in governed
    ]

    assert {asset["asset_id"] for asset in governed} == expected_ids
    assert all(response.status_code == 200 for response in responses)

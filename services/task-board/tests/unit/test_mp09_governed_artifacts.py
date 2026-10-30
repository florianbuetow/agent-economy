"""Lane T: governed V6 uploads, frozen manifests, crash recovery and archive."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from service_commons.exceptions import ServiceError

from task_board_service.services.artifact_maintenance import ArtifactMaintenance
from task_board_service.services.asset_manager import AssetManager
from task_board_service.services.streaming_upload import (
    AssetStorageQuota,
    AssetUploadLimits,
    ParsedAssetUpload,
)
from task_board_service.task_operation_contract import (
    canonical_hash,
    validate_command,
    validate_envelope,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_TASK_ID = "t-00000000-0000-4000-8000-0000000000aa"
_WORKER = "a-worker"
_POSTER = "a-poster"
_BINDING = "b" * 64
_MANIFEST_ID = f"manifest-submission-{_TASK_ID}"
_LEASE_US = 300 * 1_000_000
_DEADLINE_US = 900 * 1_000_000
_POLICY = {
    "contract_version": 1,
    "max_file_bytes": 64,
    "max_files_per_manifest": 4,
    "max_manifest_bytes": 256,
    "reservation_ttl_seconds": 300,
    "reservation_renew_before_expiry_seconds": 60,
    "max_upload_duration_seconds": 900,
}
_LIMITS = AssetUploadLimits(
    max_file_size=64,
    max_request_size=512,
    max_multipart_overhead_bytes=128,
    stream_chunk_bytes=16,
    max_header_count=8,
    max_header_size=128,
    max_boundary_bytes=70,
    max_display_filename_utf8_bytes=255,
)
_QUOTA = AssetStorageQuota(max_total_bytes_per_agent=1 << 20, max_total_bytes=1 << 20)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class FakeGateway:
    """Task store plus an in-memory model of the Gateway V6 artifact semantics."""

    def __init__(self) -> None:
        self.now_us = 1_000_000_000
        self.status = "accepted"
        self.uploads: dict[str, dict[str, Any]] = {}
        self.manifest_state = "draft"
        self.manifest_json: str | None = None
        self.finalize_failure: Exception | None = None
        self.reserve_calls = 0

    # -- task store surface -------------------------------------------------
    def get_task(self, task_id: str) -> dict[str, Any] | None:
        if task_id != _TASK_ID:
            return None
        return {
            "task_id": task_id,
            "status": self.status,
            "poster_id": _POSTER,
            "worker_id": _WORKER,
            "lifecycle_version": 1,
        }

    def get_asset(self, asset_id: str, task_id: str) -> None:
        del asset_id, task_id

    def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]:
        del task_id
        return []

    def count_assets(self, task_id: str) -> int:
        del task_id
        return 0

    # -- artifact store surface ---------------------------------------------
    def get_artifact_context(self, task_id: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "governed": True,
            "status": self.status,
            "worker_id": _WORKER,
            "lifecycle_version": 1,
            "accepted_binding_kind": "publication_contract",
            "accepted_binding_sha256": _BINDING,
            "submission_manifest_id": _MANIFEST_ID,
            "submission_manifest_state": self.manifest_state,
        }

    def _attempt(self, generation: int) -> dict[str, Any]:
        return {
            "owner_id": str(uuid.uuid4()),
            "generation": generation,
            "attempt_nonce": uuid.uuid4().hex,
            "lease_expires_at_us": self.now_us + _LEASE_US,
            "absolute_upload_deadline_us": self.now_us + _DEADLINE_US,
        }

    def _envelope(self, upload: dict[str, Any]) -> dict[str, Any]:
        result = {key: upload[key] for key in ("state", "artifact_id", "object_key")}
        if upload["state"] == "reserved":
            result["attempt"] = dict(upload["attempt"])
        return result

    def reserve_artifact_upload(
        self, policy: dict[str, int], request: dict[str, Any]
    ) -> dict[str, Any]:
        assert policy == _POLICY
        self.reserve_calls += 1
        operation_id = request["upload_operation_id"]
        existing = self.uploads.get(operation_id)
        if existing is not None:
            if existing["request"] != request:
                raise ServiceError("upload_operation_conflict", "different intent", 409, {})
            if existing["state"] != "reserved":
                return self._envelope(existing)
            if self.now_us < existing["attempt"]["lease_expires_at_us"]:
                raise ServiceError("upload_pending", "active", 409, {})
            existing["attempt"] = self._attempt(existing["attempt"]["generation"] + 1)
            return self._envelope(existing)
        if request["protocol_version"] == 2:
            assert request["manifest_kind"] == "input" and request["actor_role"] == "poster"
            if request["actor_id"] != _POSTER:
                raise ServiceError("forbidden", "Only the publication poster", 403, {})
        else:
            if self.manifest_state != "draft" or request["manifest_id"] != _MANIFEST_ID:
                raise ServiceError(
                    "manifest_mismatch", "Artifact manifest is not writable", 409, {}
                )
            if request["actor_id"] != _WORKER:
                raise ServiceError("forbidden", "Only the assigned worker may upload", 403, {})
            assert request["accepted_binding_sha256"] == _BINDING
        artifact_id = str(uuid.uuid4())
        upload = {
            "request": request,
            "state": "reserved",
            "artifact_id": artifact_id,
            "object_key": f"live/{artifact_id[:2]}/{artifact_id}.blob",
            "attempt": self._attempt(1),
        }
        self.uploads[operation_id] = upload
        return self._envelope(upload)

    def _owned(self, operation_id: str, fence: dict[str, Any]) -> dict[str, Any]:
        upload = self.uploads[operation_id]
        if upload["state"] != "reserved" or any(
            upload["attempt"][key] != fence[key]
            for key in ("owner_id", "generation", "attempt_nonce")
        ):
            raise ServiceError("stale_upload_attempt", "fence", 409, {})
        return upload

    def finalize_artifact_upload(
        self,
        upload_operation_id: str,
        policy: dict[str, int],
        fence: dict[str, Any],
        *,
        observed_size_bytes: int,
        observed_sha256: str,
    ) -> dict[str, Any]:
        assert policy == _POLICY
        if self.finalize_failure is not None:
            failure, self.finalize_failure = self.finalize_failure, None
            raise failure
        upload = self._owned(upload_operation_id, fence)
        if self.now_us >= upload["attempt"]["lease_expires_at_us"]:
            raise ServiceError("upload_lease_expired", "lease", 409, {})
        request = upload["request"]
        if (observed_size_bytes, observed_sha256) != (
            request["declared_size_bytes"],
            request["content_sha256"],
        ):
            raise ServiceError("upload_content_mismatch", "bytes", 409, {})
        if request["protocol_version"] == 1 and self.status != "accepted":
            upload["state"] = "orphaned"
            raise ServiceError("invalid_status", "Task does not accept worker uploads", 409, {})
        upload["state"] = "ready"
        upload["finalized_at"] = "2099-01-02T00:00:00Z"
        return self._envelope(upload)

    def orphan_artifact_upload(
        self,
        upload_operation_id: str,
        policy: dict[str, int],
        fence: dict[str, Any],
        *,
        failure_code: str,
    ) -> dict[str, Any]:
        del policy, failure_code
        upload = self._owned(upload_operation_id, fence)
        upload["state"] = "orphaned"
        return self._envelope(upload)

    def get_artifact_upload(self, upload_operation_id: str) -> dict[str, Any] | None:
        upload = self.uploads.get(upload_operation_id)
        if upload is None:
            return None
        request = upload["request"]
        return {
            **self._envelope(upload),
            "display_filename": request["display_filename"],
            "media_type": "text/plain; charset=utf-8",
            "declared_size_bytes": request["declared_size_bytes"],
            "content_sha256": request["content_sha256"],
        }

    def list_task_artifacts(self, task_id: str) -> dict[str, Any]:
        artifacts = []
        for upload in self.uploads.values():
            if upload["state"] not in {"ready", "archived"}:
                continue
            request = upload["request"]
            artifacts.append(
                {
                    "asset_id": f"asset-{upload['artifact_id']}",
                    "artifact_id": upload["artifact_id"],
                    "task_id": task_id,
                    "uploader_id": request["actor_id"],
                    "filename": request["display_filename"],
                    "content_type": "text/plain; charset=utf-8",
                    "size_bytes": request["declared_size_bytes"],
                    "content_hash": request["content_sha256"],
                    "uploaded_at": upload["finalized_at"],
                    "storage_path": upload.get("archive_key", upload["object_key"]),
                    "state": upload["state"],
                    "manifest_kind": "input" if request["protocol_version"] == 2 else "submission",
                }
            )
        manifests = [
            {
                "manifest_id": _MANIFEST_ID,
                "kind": "submission",
                "actor_id": _WORKER,
                "state": self.manifest_state,
                "manifest_sha256": None
                if self.manifest_json is None
                else _sha(self.manifest_json.encode()),
                "canonical_manifest_json": self.manifest_json,
            }
        ]
        return {"task_id": task_id, "artifacts": artifacts, "manifests": manifests}

    def freeze(self) -> None:
        """Model the protocol-2 submit freeze: exact sorted ready membership and hashes."""
        ready = sorted(
            (u for u in self.uploads.values() if u["state"] == "ready"),
            key=lambda u: u["artifact_id"],
        )
        self.manifest_json = json.dumps(
            {
                "artifacts": [
                    {
                        "artifact_id": u["artifact_id"],
                        "sha256": u["request"]["content_sha256"],
                        "byte_size": u["request"]["declared_size_bytes"],
                    }
                    for u in ready
                ]
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.manifest_state = "frozen"

    def get_artifact_recovery(self) -> dict[str, Any]:
        reserved = [
            {
                "upload_operation_id": operation_id,
                "object_key": upload["object_key"],
                "declared_size_bytes": upload["request"]["declared_size_bytes"],
                "content_sha256": upload["request"]["content_sha256"],
                "canonical_request_json": json.dumps(upload["request"]),
                "attempt": dict(upload["attempt"]),
            }
            for operation_id, upload in self.uploads.items()
            if upload["state"] == "reserved"
        ]
        keys = {
            upload.get("archive_key", upload["object_key"])
            for upload in self.uploads.values()
            if upload["state"] in {"reserved", "ready", "archived"}
        }
        return {"reserved": reserved, "referenced_object_keys": sorted(keys)}

    def list_archive_candidates(self, archive_after_seconds: int, limit: int) -> dict[str, Any]:
        del archive_after_seconds
        if self.status not in {"approved", "ruled", "cancelled", "expired"}:
            return {"candidates": []}
        return {
            "candidates": [
                {
                    "kind": "artifact",
                    "id": upload["artifact_id"],
                    "task_id": _TASK_ID,
                    "live_object_key": upload["object_key"],
                    "archive_object_key": "archive/" + upload["object_key"].removeprefix("live/"),
                    "size_bytes": upload["request"]["declared_size_bytes"],
                    "content_hash": upload["request"]["content_sha256"],
                }
                for upload in self.uploads.values()
                if upload["state"] == "ready"
            ][:limit]
        }

    def finalize_archive(self, candidate: dict[str, Any]) -> dict[str, Any]:
        for upload in self.uploads.values():
            if upload["artifact_id"] == candidate["id"]:
                upload["state"] = "archived"
                upload["archive_key"] = candidate["archive_object_key"]
        return {"state": "archived", "storage_path": candidate["archive_object_key"]}


def _manager(gateway: FakeGateway, root: Path, *, signer: str = _WORKER) -> AssetManager:
    validator = AsyncMock()
    validator.validate_jws_token = AsyncMock(
        return_value={"action": "upload_asset", "task_id": _TASK_ID, "_signer_id": signer}
    )
    deadline = AsyncMock()

    async def unchanged(task: dict[str, Any]) -> dict[str, Any]:
        return task

    deadline.evaluate_deadline = AsyncMock(side_effect=unchanged)
    manager = AssetManager(
        store=gateway,  # type: ignore[arg-type]
        token_validator=validator,
        deadline_evaluator=deadline,
        asset_storage_path=str(root),
        max_file_size=64,
        max_files_per_task=4,
    )
    manager.configure_artifacts(gateway, dict(_POLICY))
    return manager


@pytest.fixture
def stream_body(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace multipart parsing with a body/filename the test chooses."""
    current: dict[str, Any] = {"body": b"", "filename": "answer.txt", "fail_after_write": False}

    async def parsed_upload(
        _request: object,
        _limits: AssetUploadLimits,
        _content_length: int | None,
        write_chunk: Any,
    ) -> ParsedAssetUpload:
        await write_chunk(current["body"])
        if current["fail_after_write"]:
            raise ConnectionResetError("client vanished mid-upload")
        return ParsedAssetUpload(
            filename=current["filename"],
            content_type="text/plain",
            size_bytes=len(current["body"]),
            content_hash=_sha(current["body"]),
        )

    monkeypatch.setattr(
        "task_board_service.services.asset_manager.stream_asset_upload", parsed_upload
    )
    return current


_REQUEST = type("RequestStub", (), {"headers": {"content-type": "multipart/form-data"}})()


async def _upload(
    manager: AssetManager,
    stream_body: dict[str, Any],
    body: bytes,
    filename: str,
    *,
    key: str | None = None,
) -> dict[str, Any]:
    stream_body.update(body=body, filename=filename)
    return await manager.upload_streaming_asset(
        task_id=_TASK_ID,
        token="token",
        request=_REQUEST,  # type: ignore[arg-type]
        limits=_LIMITS,
        quota=_QUOTA,
        idempotency_key=key,
    )


def _files(root: Path, top: str) -> list[str]:
    base = root / top
    if not base.exists():
        return []
    return sorted(str(path.relative_to(root)) for path in base.rglob("*") if path.is_file())


async def test_two_file_submission_manifest_hashes_match_downloaded_bytes(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    first = await _upload(manager, stream_body, b"alpha answer\n", "answer.txt")
    second = await _upload(manager, stream_body, b'{"x": 1}\n', "data.json")
    assert first["asset_id"].startswith("asset-") and second["content_hash"] == _sha(b'{"x": 1}\n')

    arguments = manager.submission_arguments(_TASK_ID, _WORKER)
    assert arguments == {
        "worker_id": _WORKER,
        "manifest_id": _MANIFEST_ID,
        "accepted_binding_kind": "publication_contract",
        "accepted_binding_sha256": _BINDING,
    }
    gateway.freeze()

    listing = await manager.list_assets(_TASK_ID)
    manifest = gateway.list_task_artifacts(_TASK_ID)["manifests"][0]
    frozen = {
        item["artifact_id"]: item["sha256"]
        for item in json.loads(manifest["canonical_manifest_json"])["artifacts"]
    }
    assert len(listing["assets"]) == 2 and len(frozen) == 2
    for asset in listing["assets"]:
        content, _type, _name = await manager.download_asset(_TASK_ID, asset["asset_id"])
        assert _sha(content) == frozen[asset["asset_id"].removeprefix("asset-")]
    assert _files(root, ".staging") == []


async def test_upload_set_cannot_change_after_submission(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    await _upload(manager, stream_body, b"alpha\n", "answer.txt")
    gateway.freeze()
    live_before = _files(root, "live")
    with pytest.raises(ServiceError) as raised:
        await _upload(manager, stream_body, b"late\n", "late.txt")
    assert raised.value.error == "manifest_mismatch"
    assert _files(root, "live") == live_before
    assert _files(root, ".staging") == []


async def test_metadata_rejection_leaves_no_ready_row_or_blob(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root, signer="a-stranger")
    manager_task = gateway.get_task(_TASK_ID)
    assert manager_task is not None
    gateway.get_task = lambda _task_id: {**manager_task, "worker_id": "a-stranger"}  # type: ignore[method-assign]
    with pytest.raises(ServiceError) as raised:
        await _upload(manager, stream_body, b"alpha\n", "answer.txt")
    assert raised.value.status_code == 403
    assert gateway.uploads == {}
    assert _files(root, "live") == [] and _files(root, ".staging") == []


async def test_unsupported_evidence_format_is_rejected_before_reservation(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    with pytest.raises(ServiceError) as raised:
        await _upload(manager, stream_body, b"\x00\x01", "binary.exe")
    assert raised.value.status_code == 415
    assert gateway.reserve_calls == 0
    assert _files(root, ".staging") == []


async def test_finalize_rejection_that_orphans_removes_installed_blob(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    original = gateway.reserve_artifact_upload

    def reserve_then_close(policy: dict[str, int], request: dict[str, Any]) -> dict[str, Any]:
        envelope = original(policy, request)
        gateway.status = "expired"
        return envelope

    gateway.reserve_artifact_upload = reserve_then_close  # type: ignore[method-assign]
    with pytest.raises(ServiceError) as raised:
        await _upload(manager, stream_body, b"alpha\n", "answer.txt")
    assert raised.value.error == "invalid_status"
    assert [u["state"] for u in gateway.uploads.values()] == ["orphaned"]
    assert _files(root, "live") == []


async def test_killed_after_install_restart_recovers_and_retry_completes_once(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    gateway.finalize_failure = ConnectionError("gateway response lost")
    with pytest.raises(ServiceError) as raised:
        await _upload(manager, stream_body, b"alpha\n", "answer.txt", key="retry-1")
    assert raised.value.error == "artifact_finalize_unknown"
    assert len(_files(root, "live")) == 1

    # Crash leftovers: a stray partial staging file and an unreferenced live blob.
    (root / ".staging" / "dead.part.upload").write_bytes(b"partial")
    stray = root / "live" / "zz" / "stray.blob"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"orphan")

    manager.close()
    gateway.now_us += _LEASE_US + 1
    restarted = _manager(gateway, root)
    # Startup sweep (stale age None): the Gateway lease clock is the fake's clock.
    counts = restarted.recover_storage(now_us=gateway.now_us, stale_staging_seconds=None)
    assert counts["reservations_finalized"] == 1
    assert counts["staging_removed"] == 1 and counts["orphans_removed"] == 1
    assert _files(root, ".staging") == []
    assert [u["state"] for u in gateway.uploads.values()] == ["ready"]

    reserves_before = gateway.reserve_calls
    retried = await _upload(restarted, stream_body, b"alpha\n", "answer.txt", key="retry-1")
    assert gateway.reserve_calls == reserves_before
    assert len(gateway.uploads) == 1
    assert retried["content_hash"] == _sha(b"alpha\n")
    assert len(_files(root, "live")) == 1


async def test_killed_mid_stream_leaves_no_orphan_and_retry_completes_once(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    stream_body["fail_after_write"] = True
    with pytest.raises(ServiceError):
        await _upload(manager, stream_body, b"alpha\n", "answer.txt", key="retry-2")
    stream_body["fail_after_write"] = False
    assert gateway.uploads == {}
    assert _files(root, ".staging") == []

    ArtifactMaintenance(
        manager, stale_file_seconds=900, archive_after_seconds=1, archive_batch_size=10
    ).run_once(startup=True)
    first = await _upload(manager, stream_body, b"alpha\n", "answer.txt", key="retry-2")
    again = await _upload(manager, stream_body, b"alpha\n", "answer.txt", key="retry-2")
    assert first == again
    assert len(gateway.uploads) == 1
    assert len(_files(root, "live")) == 1


async def test_expired_reservation_past_deadline_is_orphaned_and_bytes_removed(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    gateway.finalize_failure = ConnectionError("lost")
    with pytest.raises(ServiceError):
        await _upload(manager, stream_body, b"alpha\n", "answer.txt")
    gateway.now_us += _DEADLINE_US + 1
    counts = manager.recover_storage(now_us=gateway.now_us, stale_staging_seconds=900)
    assert counts["reservations_orphaned"] == 1
    assert [u["state"] for u in gateway.uploads.values()] == ["orphaned"]
    assert _files(root, "live") == []


async def test_archive_moves_terminal_bytes_and_they_survive_restart(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    asset = await _upload(manager, stream_body, b"alpha\n", "answer.txt")
    gateway.status = "approved"

    archived = manager.archive_terminal_artifacts(archive_after_seconds=259_200, limit=10)
    assert archived == 1
    assert _files(root, "live") == []
    assert len(_files(root, "archive")) == 1

    manager.close()
    restarted = _manager(gateway, root)
    ArtifactMaintenance(
        restarted, stale_file_seconds=900, archive_after_seconds=259_200, archive_batch_size=10
    ).run_once(startup=True)
    assert len(_files(root, "archive")) == 1
    content, _type, _name = await restarted._download_asset(
        _TASK_ID, asset["asset_id"], allow_terminal=True
    )
    assert content == b"alpha\n"


async def test_terminal_reads_court_and_poster_allowed_public_410_stranger_403(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    asset = await _upload(manager, stream_body, b"alpha\n", "answer.txt")
    gateway.status = "ruled"
    manager.archive_terminal_artifacts(archive_after_seconds=259_200, limit=10)

    def reader(signer: str, *, platform: bool) -> None:
        validator = manager._token_validator
        validator.validate_asset_read_token = AsyncMock(  # type: ignore[method-assign]
            return_value=(
                {"task_id": _TASK_ID, "asset_id": asset["asset_id"], "_signer_id": signer},
                platform,
            )
        )
        validator.is_configured_operator = AsyncMock(return_value=False)  # type: ignore[method-assign]

    reader("court-platform", platform=True)
    court, _t, _n = await manager.download_asset_for_reader(_TASK_ID, asset["asset_id"], "tok")
    assert court == b"alpha\n"
    reader(_POSTER, platform=False)
    poster, _t, _n = await manager.download_asset_for_reader(_TASK_ID, asset["asset_id"], "tok")
    assert poster == b"alpha\n"
    reader("a-stranger", platform=False)
    with pytest.raises(ServiceError) as stranger:
        await manager.download_asset_for_reader(_TASK_ID, asset["asset_id"], "tok")
    assert stranger.value.status_code == 403
    with pytest.raises(ServiceError) as public:
        await manager.download_asset(_TASK_ID, asset["asset_id"])
    assert public.value.status_code == 410


def test_ungoverned_task_keeps_protocol_one(tmp_path: Path) -> None:
    gateway = FakeGateway()
    gateway.get_artifact_context = lambda task_id: {  # type: ignore[method-assign]
        "task_id": task_id,
        "governed": False,
    }
    manager = _manager(gateway, tmp_path / "assets")
    assert manager.submission_arguments(_TASK_ID, _WORKER) is None


def _v2_command() -> dict[str, Any]:
    return {
        "protocol_version": 2,
        "operation_id": "op-00000000-0000-4000-8000-0000000000ab",
        "task_id": _TASK_ID,
        "command": "submit_deliverable",
        "expected_status": "accepted",
        "expected_lifecycle_version": 1,
        "actor_kind": "agent",
        "actor_id": _WORKER,
        "arguments": {
            "worker_id": _WORKER,
            "manifest_id": _MANIFEST_ID,
            "accepted_binding_kind": "publication_contract",
            "accepted_binding_sha256": _BINDING,
        },
    }


def test_contract_accepts_protocol_two_submit_and_its_manifest_result() -> None:
    command = validate_command(_v2_command())
    envelope = {
        "protocol_version": 2,
        "operation_id": command.operation_id,
        "task_id": _TASK_ID,
        "command": "submit_deliverable",
        "request_sha256": canonical_hash(command.model_dump(exclude={"operation_id"})),
        "operation_state": "completed",
        "source_status": "accepted",
        "source_lifecycle_version": 1,
        "target_status": "submitted",
        "target_lifecycle_version": 2,
        "effect_kind": "none",
        "effect_state": "none",
        "effect_operation_id": None,
        "effect_result_reference": None,
        "effect_intent": None,
        "event_id": 7,
        "result": {
            "task_id": _TASK_ID,
            "command": "submit_deliverable",
            "status": "submitted",
            "lifecycle_version": 2,
            "event_id": 7,
            "worker_id": _WORKER,
            "manifest_id": _MANIFEST_ID,
            "manifest_sha256": "c" * 64,
        },
    }
    validated = validate_envelope(
        envelope, status_code=200, operation_id=command.operation_id, command=command
    )
    assert validated["result"]["manifest_sha256"] == "c" * 64


def test_contract_rejects_protocol_two_for_other_commands() -> None:
    payload = _v2_command()
    payload["arguments"] = {"worker_id": _WORKER}
    with pytest.raises(ServiceError):
        validate_command(payload)
    payload = _v2_command()
    payload["command"] = "approve_task"
    with pytest.raises(ServiceError):
        validate_command(payload)


def test_staging_age_gate_keeps_recent_files_during_periodic_sweep(tmp_path: Path) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    staging = root / ".staging"
    staging.mkdir(parents=True, exist_ok=True)
    recent = staging / "recent.upload"
    recent.write_bytes(b"in flight")
    old = staging / "old.upload"
    old.write_bytes(b"stale")
    os.utime(old, (1, 1))
    counts = manager.recover_storage(now_us=gateway.now_us, stale_staging_seconds=900)
    assert counts["staging_removed"] == 1
    assert recent.exists() and not old.exists()


async def _upload_input(
    manager: AssetManager, stream_body: dict[str, Any], body: bytes, *, key: str | None
) -> dict[str, Any]:
    stream_body.update(body=body, filename="instructions.md")
    return await manager.upload_input_artifact(
        publication_operation_id="op-00000000-0000-4000-8000-0000000000cc",
        task_id=_TASK_ID,
        manifest_id="manifest-input-x",
        poster_id=_POSTER,
        request=_REQUEST,  # type: ignore[arg-type]
        limits=_LIMITS,
        quota=_QUOTA,
        idempotency_key=key,
    )


async def test_input_upload_is_reserved_installed_and_replayed_once(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    root = tmp_path / "assets"
    manager = _manager(gateway, root)
    assert manager.artifact_policy() == _POLICY
    first = await _upload_input(manager, stream_body, b"# do this\n", key="in-1")
    again = await _upload_input(manager, stream_body, b"# do this\n", key="in-1")
    assert first == again
    assert first["role"] == "input" and first["content_hash"] == _sha(b"# do this\n")
    assert len(gateway.uploads) == 1
    request = next(iter(gateway.uploads.values()))["request"]
    assert (
        request["resource"]
        == "POST /task-publications/op-00000000-0000-4000-8000-0000000000cc/inputs"
    )
    assert len(_files(root, "live")) == 1 and _files(root, ".staging") == []


def test_artifact_policy_requires_configuration(tmp_path: Path) -> None:
    manager = AssetManager(
        store=FakeGateway(),  # type: ignore[arg-type]
        token_validator=AsyncMock(),
        deadline_evaluator=AsyncMock(),
        asset_storage_path=str(tmp_path / "assets"),
        max_file_size=64,
        max_files_per_task=4,
    )
    with pytest.raises(ServiceError) as raised:
        manager.artifact_policy()
    assert raised.value.status_code == 503


def _as_reader(manager: AssetManager, signer: str, asset_id: str | None) -> None:
    validator = manager._token_validator
    validator.validate_asset_read_token = AsyncMock(  # type: ignore[method-assign]
        return_value=({"task_id": _TASK_ID, "asset_id": asset_id, "_signer_id": signer}, False)
    )
    validator.is_configured_operator = AsyncMock(return_value=False)  # type: ignore[method-assign]


async def test_signed_strangers_read_inputs_only_while_nonterminal(
    tmp_path: Path, stream_body: dict[str, Any]
) -> None:
    gateway = FakeGateway()
    manager = _manager(gateway, tmp_path / "assets")
    await _upload_input(manager, stream_body, b"# spec\n", key=None)
    output = await _upload(manager, stream_body, b"answer\n", "answer.txt")
    input_id = next(
        a["asset_id"]
        for a in (await manager.list_assets(_TASK_ID))["assets"]
        if a["role"] == "input"
    )

    _as_reader(manager, "a-bidder", None)
    listing = await manager.list_assets_for_reader(_TASK_ID, "tok")
    assert [a["role"] for a in listing["assets"]] == ["input"]
    _as_reader(manager, "a-bidder", input_id)
    content, _t, _n = await manager.download_asset_for_reader(_TASK_ID, input_id, "tok")
    assert content == b"# spec\n"
    _as_reader(manager, "a-bidder", output["asset_id"])
    with pytest.raises(ServiceError) as hidden:
        await manager.download_asset_for_reader(_TASK_ID, output["asset_id"], "tok")
    assert hidden.value.status_code == 403

    gateway.status = "approved"
    _as_reader(manager, "a-bidder", input_id)
    with pytest.raises(ServiceError) as closed:
        await manager.download_asset_for_reader(_TASK_ID, input_id, "tok")
    assert closed.value.status_code == 403
    _as_reader(manager, _POSTER, None)
    poster_listing = await manager.list_assets_for_reader(_TASK_ID, "tok")
    assert sorted(a["role"] for a in poster_listing["assets"]) == ["input", "output"]

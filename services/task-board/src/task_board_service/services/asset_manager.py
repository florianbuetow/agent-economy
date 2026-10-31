"""Asset upload/list/download management for task deliverables."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from task_board_service.services.errors import AssetInsertRejectedError
from task_board_service.services.streaming_upload import (
    AssetStorageQuota,
    AssetUploadLimits,
    ParsedAssetUpload,
    stream_asset_upload,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from starlette.requests import Request

    from task_board_service.services.artifact_store import ArtifactStore
    from task_board_service.services.deadline_evaluator import DeadlineEvaluator
    from task_board_service.services.protocol import TaskStorageInterface
    from task_board_service.services.token_validator import TokenValidator


_CLOSE_ON_EXEC_FLAG = os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOSE_ON_EXEC_FLAG
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | _CLOSE_ON_EXEC_FLAG
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOSE_ON_EXEC_FLAG
_IDEMPOTENCY_KEY_PATTERN = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_IDEMPOTENCY_NAMESPACE = uuid.UUID("6f1c2a0e-6b1d-4c39-9a51-2f7d1d0a7a26")
_TERMINAL_STATUSES = frozenset({"approved", "ruled", "cancelled", "expired"})
_EVIDENCE_FORMATS: dict[str, str] = {
    ".txt": "text",
    ".md": "text",
    ".json": "json",
    ".csv": "csv",
    ".py": "source",
    ".js": "source",
    ".ts": "source",
    ".html": "source",
    ".css": "source",
    ".sql": "source",
    ".sh": "source",
}
_EVIDENCE_MEDIA_TYPES: dict[str, str] = {
    "text": "text/plain; charset=utf-8",
    "source": "text/plain; charset=utf-8",
    "json": "application/json",
    "csv": "text/csv; charset=utf-8",
}
_RESERVE_RESOURCE = "POST /board/artifact-uploads/reserve"


def _evidence_format(filename: str) -> str:
    """Map a display filename to one supported evidence format (Q-M1 E-A allowlist)."""
    suffix = Path(filename).suffix.lower()
    evidence_format = _EVIDENCE_FORMATS.get(suffix)
    if evidence_format is None:
        raise ServiceError(
            "unsupported_evidence_format",
            "Only text, source, JSON and CSV evidence files are accepted",
            415,
            {"supported_extensions": sorted(_EVIDENCE_FORMATS)},
        )
    return evidence_format


def _upload_operation_id(task_id: str, signer_id: str, idempotency_key: str | None) -> str:
    """Return a UUIDv4-shaped operation id, deterministic for an idempotency key."""
    if idempotency_key is None:
        return str(uuid.uuid4())
    derived = uuid.uuid5(
        _IDEMPOTENCY_NAMESPACE, f"v6\x00{task_id}\x00{signer_id}\x00{idempotency_key}"
    )
    return str(uuid.UUID(bytes=derived.bytes, version=4))


def _open_parent(root_descriptor: int, parts: tuple[str, ...], *, create: bool) -> list[int]:
    """Open (optionally creating) every directory of a storage key; caller closes them."""
    opened: list[int] = []
    parent = root_descriptor
    try:
        for component in parts[:-1]:
            if create:
                parent = _open_or_create_directory(parent, component)
            else:
                parent = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
            opened.append(parent)
    except BaseException:
        for descriptor in reversed(opened):
            os.close(descriptor)
        raise
    return opened


def _file_digest(parent_descriptor: int, name: str) -> tuple[int, str] | None:
    """Return (size, sha256) of a regular file without following links, or None."""
    try:
        descriptor = os.open(name, _FILE_READ_FLAGS, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 64 * 1024):
            digest.update(chunk)
            size += len(chunk)
        return size, digest.hexdigest()
    finally:
        os.close(descriptor)


def _agent_storage_key(agent_id: str) -> str:
    """Map an uploader id to a fixed-width directory name that is always path-safe."""
    return hashlib.sha256(agent_id.encode("utf-8")).hexdigest()[:32]


def _validate_idempotency_key(key: str) -> None:
    if _IDEMPOTENCY_KEY_PATTERN.fullmatch(key) is None:
        raise ServiceError(
            "invalid_idempotency_key",
            "Idempotency-Key must be 1-128 characters from [A-Za-z0-9._:-]",
            400,
            {},
        )


def _finalize_unknown() -> ServiceError:
    return ServiceError(
        "artifact_finalize_unknown",
        "Upload outcome is unknown; retry with the same Idempotency-Key",
        503,
        {},
    )


def _read_forbidden() -> ServiceError:
    return ServiceError(
        "forbidden",
        "Only the task poster, assigned worker, platform or operator may read task assets",
        403,
        {},
    )


def _quota_exceeded() -> ServiceError:
    return ServiceError(
        "storage_quota_exceeded",
        "Upload would exceed the configured asset storage quota",
        413,
        {},
    )


def _stored_bytes(root_descriptor: int, parts: tuple[str, ...]) -> int:
    """Sum regular-file sizes below one storage directory without following links."""
    opened: list[int] = []
    parent = root_descriptor
    try:
        for component in parts:
            try:
                parent = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent)
            except FileNotFoundError:
                return 0
            opened.append(parent)
        total = 0
        for _path, _dirs, files, directory_fd in os.fwalk(
            ".", dir_fd=parent, follow_symlinks=False
        ):
            for name in files:
                entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISREG(entry.st_mode):
                    total += entry.st_size
        return total
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _invalid_filename(filename: str) -> bool:
    """Return whether a display filename is unsafe for metadata or headers."""
    if (
        filename in {"", ".", ".."}
        or "/" in filename
        or "\\" in filename
        or (len(filename) >= 2 and filename[0].isalpha() and filename[1] == ":")
    ):
        return True
    return any(
        ord(character) <= 0x1F
        or 0x7F <= ord(character) <= 0x9F
        or 0xD800 <= ord(character) <= 0xDFFF
        or 0x202A <= ord(character) <= 0x202E
        or 0x2066 <= ord(character) <= 0x2069
        for character in filename
    )


def _safe_storage_parts(storage_path: str) -> tuple[str, ...] | None:
    """Parse one safe relative POSIX storage key without normalizing it."""
    if (
        not storage_path
        or storage_path.startswith("/")
        or "\\" in storage_path
        or "\x00" in storage_path
    ):
        return None
    parts = tuple(storage_path.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return parts


def _bootstrap_storage_root(path: Path) -> int:
    """Resolve first, then create and pin missing components without following links."""
    if not path.is_absolute():
        msg = "Canonical asset storage path must be absolute"
        raise ValueError(msg)

    descriptor = os.open(os.sep, _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            next_descriptor = _open_or_create_directory(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        root_stat = os.fstat(descriptor)
        if root_stat.st_uid != os.geteuid():
            msg = "Asset storage root must be owned by the effective service user"
            raise ValueError(msg)
        if stat.S_IMODE(root_stat.st_mode) != 0o700:
            msg = "Asset storage root must have mode 0700"
            raise ValueError(msg)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_or_create_directory(parent_descriptor: int, name: str) -> int:
    """Open a direct child directory, creating it privately when absent."""
    with suppress(FileExistsError):
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
    return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)


def _write_all(descriptor: int, content: bytes) -> None:
    """Write the complete in-memory V5 payload to an already-owned descriptor."""
    view = memoryview(content)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count == 0:
            raise OSError("Asset write made no progress")
        written += count


def _unlink_owned_file(parent_descriptor: int, name: str) -> None:
    """Remove one exact owned entry and durably record the directory update."""
    try:
        os.unlink(name, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return
    os.fsync(parent_descriptor)


def _read_for_integrity_check(descriptor: int, expected_size: int) -> bytes:
    """Read no more than one byte beyond the authoritative expected size."""
    chunks: list[bytes] = []
    remaining = expected_size + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class AssetManager:
    """Manages asset upload, listing, and download for tasks."""

    def __init__(
        self,
        store: TaskStorageInterface,
        token_validator: TokenValidator,
        deadline_evaluator: DeadlineEvaluator,
        asset_storage_path: str,
        max_file_size: int,
        max_files_per_task: int,
    ) -> None:
        self._store = store
        self._token_validator = token_validator
        self._deadline_evaluator = deadline_evaluator
        self._max_file_size = max_file_size
        self._max_files_per_task = max_files_per_task
        self._descriptor_lock = threading.Lock()
        self._root_descriptor: int | None = None
        self._artifacts: ArtifactStore | None = None
        self._artifact_policy: dict[str, int] | None = None

        canonical_storage_path = Path(asset_storage_path).absolute().resolve(strict=False)
        self._root_descriptor = _bootstrap_storage_root(canonical_storage_path)

    def close(self) -> None:
        """Close the pinned storage root exactly once."""
        with self._descriptor_lock:
            descriptor = self._root_descriptor
            self._root_descriptor = None
            if descriptor is not None:
                os.close(descriptor)

    def __del__(self) -> None:
        """Release an owned descriptor when legacy constructors omit explicit cleanup."""
        with suppress(OSError):
            self.close()

    def _borrow_root_descriptor(self) -> int:
        with self._descriptor_lock:
            descriptor = self._root_descriptor
            if descriptor is None:
                raise ServiceError("service_not_ready", "Asset storage is closed", 503, {})
            return os.dup(descriptor)

    @property
    def max_file_size(self) -> int:
        """Return the configured file-size limit used by every upload path."""
        return self._max_file_size

    def configure_artifacts(self, store: ArtifactStore, policy: dict[str, int]) -> None:
        """Enable governed V6 uploads, manifest reads, recovery and archive.

        ``policy`` is the complete Gateway ``ArtifactPolicy``; it is sent with every
        mutating Gateway call so the Gateway holds no artifact defaults.
        """
        self._artifacts = store
        self._artifact_policy = dict(policy)

    def _governed_context(self, task_id: str) -> dict[str, Any] | None:
        if self._artifacts is None:
            return None
        context = self._artifacts.get_artifact_context(task_id)
        return context if context.get("governed") is True else None

    def submission_binding(self, task_id: str) -> dict[str, str] | None:
        """Public binding a governed submission must match, once the task is accepted."""
        context = self._governed_context(task_id)
        if context is None or not context.get("accepted_binding_sha256"):
            return None
        return {
            "manifest_id": str(context["submission_manifest_id"]),
            "accepted_binding_kind": str(context["accepted_binding_kind"]),
            "accepted_binding_sha256": str(context["accepted_binding_sha256"]),
        }

    def submission_arguments(self, task_id: str, worker_id: str) -> dict[str, str | int] | None:
        """Return protocol-2 submit arguments for a governed task, else None (protocol 1).

        The manifest id and accepted binding come from the Gateway, so a protocol-2
        submit freezes exactly the worker's ready uploads bound to the accepted terms.
        """
        context = self._governed_context(task_id)
        if context is None:
            return None
        return {
            "worker_id": worker_id,
            "manifest_id": str(context["submission_manifest_id"]),
            "accepted_binding_kind": str(context["accepted_binding_kind"]),
            "accepted_binding_sha256": str(context["accepted_binding_sha256"]),
        }

    def _require_artifacts(self) -> tuple[ArtifactStore, dict[str, int]]:
        if self._artifacts is None or self._artifact_policy is None:
            raise ServiceError("service_not_ready", "Artifact store is not configured", 503, {})
        return self._artifacts, self._artifact_policy

    @staticmethod
    def _validate_display_filename(filename: str) -> None:
        if _invalid_filename(filename):
            raise ServiceError(
                "invalid_filename",
                "Filename contains unsafe characters",
                400,
                {},
            )

    async def _authorize_upload(self, task_id: str, token: str) -> tuple[str, int]:
        signer_id = await self._authenticate_upload(task_id, token)
        lifecycle_version = await self._check_upload_eligibility(task_id, signer_id)
        return signer_id, lifecycle_version

    async def _authenticate_upload(self, task_id: str, token: str) -> str:
        payload = await self._token_validator.validate_jws_token(
            token,
            "upload_asset",
            expected_resource=f"POST /tasks/{task_id}/assets",
        )
        signer_id: str = payload["_signer_id"]

        if "task_id" not in payload:
            raise ServiceError("invalid_payload", "Missing required field: task_id", 400, {})
        if payload["task_id"] != task_id:
            raise ServiceError(
                "invalid_payload",
                "task_id in payload does not match URL path",
                400,
                {},
            )
        if "worker_id" in payload and signer_id != payload["worker_id"]:
            raise ServiceError("forbidden", "Signer does not match worker_id", 403, {})
        return signer_id

    def _remaining_quota_bytes(self, signer_id: str, quota: AssetStorageQuota) -> int:
        """Return how many more bytes the signer may store; reject when none remain."""
        root_descriptor = self._borrow_root_descriptor()
        try:
            agent_bytes = _stored_bytes(root_descriptor, ("live", _agent_storage_key(signer_id)))
            total_bytes = _stored_bytes(root_descriptor, ("live",))
        finally:
            os.close(root_descriptor)
        remaining = min(
            quota.max_total_bytes_per_agent - agent_bytes,
            quota.max_total_bytes - total_bytes,
        )
        if remaining <= 0:
            raise _quota_exceeded()
        return remaining

    async def _check_upload_eligibility(self, task_id: str, signer_id: str) -> int:
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})
        task = await self._deadline_evaluator.evaluate_deadline(task)
        if task["status"] != "accepted":
            raise ServiceError(
                "invalid_status",
                f"Cannot upload assets to task in '{task['status']}' status, must be 'accepted'",
                409,
                {},
            )
        if signer_id != task["worker_id"]:
            raise ServiceError(
                "forbidden",
                "Only the assigned worker can upload assets",
                403,
                {},
            )
        if self.count_assets(task_id) >= self._max_files_per_task:
            raise ServiceError(
                "too_many_assets",
                f"Maximum of {self._max_files_per_task} assets per task reached",
                409,
                {},
            )
        lifecycle_version = task.get("lifecycle_version")
        if (
            type(lifecycle_version) is not int
            or lifecycle_version < 0
            or lifecycle_version > (1 << 63) - 1
        ):
            raise ServiceError(
                "invalid_gateway_response",
                "Gateway returned an invalid response",
                502,
                {},
            )
        return lifecycle_version

    @staticmethod
    def _asset_response(asset: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in asset.items() if key != "storage_path"}

    def _delete_storage_key(self, storage_path: str) -> None:
        parts = _safe_storage_parts(storage_path)
        if parts is None:
            return
        root_descriptor = self._borrow_root_descriptor()
        opened_directories: list[int] = []
        parent_descriptor = root_descriptor
        try:
            for component in parts[:-1]:
                parent_descriptor = os.open(component, _DIRECTORY_FLAGS, dir_fd=parent_descriptor)
                opened_directories.append(parent_descriptor)
            _unlink_owned_file(parent_descriptor, parts[-1])
        finally:
            for descriptor in reversed(opened_directories):
                os.close(descriptor)
            os.close(root_descriptor)

    def _persist_published_asset(
        self,
        asset: dict[str, Any],
        *,
        expected_lifecycle_version: int,
    ) -> None:
        try:
            self._store.insert_asset(
                asset,
                expected_lifecycle_version=expected_lifecycle_version,
                max_files_per_task=self._max_files_per_task,
            )
        except Exception as insert_error:
            try:
                observed = self._store.get_asset(str(asset["asset_id"]), str(asset["task_id"]))
            except Exception:
                raise insert_error from None
            if observed == asset:
                return
            if observed is None and isinstance(
                insert_error,
                (AssetInsertRejectedError, ValueError),
            ):
                self._delete_storage_key(str(asset["storage_path"]))
            if (
                isinstance(insert_error, AssetInsertRejectedError)
                and insert_error.error is not None
            ):
                raise ServiceError(
                    insert_error.error,
                    insert_error.message,
                    insert_error.status_code,
                    insert_error.details,
                ) from None
            raise

    @staticmethod
    def _asset_metadata(
        *,
        artifact_uuid: uuid.UUID,
        task_id: str,
        signer_id: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        content_hash: str,
    ) -> dict[str, Any]:
        return {
            "asset_id": f"asset-{artifact_uuid}",
            "task_id": task_id,
            "uploader_id": signer_id,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "storage_path": (
                f"live/{_agent_storage_key(signer_id)}/{artifact_uuid.hex[:2]}/{artifact_uuid}.blob"
            ),
            "content_hash": content_hash,
            "uploaded_at": _now_iso(),
        }

    async def upload_asset(
        self,
        task_id: str,
        token: str,
        file_content: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        """Upload a deliverable asset."""
        signer_id, lifecycle_version = await self._authorize_upload(task_id, token)
        if len(file_content) > self._max_file_size:
            raise ServiceError(
                "file_too_large",
                f"File exceeds maximum size of {self._max_file_size} bytes",
                413,
                {},
            )
        self._validate_display_filename(filename)

        artifact_uuid = uuid.uuid4()
        content_hash = hashlib.sha256(file_content).hexdigest()

        root_descriptor = self._borrow_root_descriptor()
        live_descriptor: int | None = None
        agent_descriptor: int | None = None
        shard_descriptor: int | None = None
        file_descriptor: int | None = None
        try:
            live_descriptor = _open_or_create_directory(root_descriptor, "live")
            os.fsync(root_descriptor)
            agent_descriptor = _open_or_create_directory(
                live_descriptor, _agent_storage_key(signer_id)
            )
            os.fsync(live_descriptor)
            shard_descriptor = _open_or_create_directory(agent_descriptor, artifact_uuid.hex[:2])
            os.fsync(agent_descriptor)
            file_descriptor = os.open(
                f"{artifact_uuid}.blob",
                _FILE_WRITE_FLAGS,
                0o600,
                dir_fd=shard_descriptor,
            )
            _write_all(file_descriptor, file_content)
            os.fsync(file_descriptor)
            os.fsync(shard_descriptor)
        except FileExistsError as exc:
            raise ServiceError(
                "artifact_destination_collision",
                "Generated artifact destination already exists",
                409,
                {},
            ) from exc
        except OSError as exc:
            raise ServiceError(
                "storage_unavailable",
                "Artifact storage is unavailable",
                503,
                {},
            ) from exc
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            if shard_descriptor is not None:
                os.close(shard_descriptor)
            if agent_descriptor is not None:
                os.close(agent_descriptor)
            if live_descriptor is not None:
                os.close(live_descriptor)
            os.close(root_descriptor)

        asset = self._asset_metadata(
            artifact_uuid=artifact_uuid,
            task_id=task_id,
            signer_id=signer_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(file_content),
            content_hash=content_hash,
        )
        self._persist_published_asset(
            asset,
            expected_lifecycle_version=lifecycle_version,
        )
        return self._asset_response(asset)

    async def upload_streaming_asset(
        self,
        *,
        task_id: str,
        token: str,
        request: Request,
        limits: AssetUploadLimits,
        quota: AssetStorageQuota,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Authenticate first, then stage and atomically publish one streamed asset.

        A retry carrying the same ``idempotency_key`` from the same signer for the
        same task returns the already-stored asset without reading the body.
        """
        if idempotency_key is not None:
            _validate_idempotency_key(idempotency_key)
        signer_id = await self._authenticate_upload(task_id, token)
        governed = self._governed_context(task_id)
        if governed is not None:
            return await self._upload_governed(
                task_id=task_id,
                signer_id=signer_id,
                request=request,
                limits=limits,
                quota=quota,
                idempotency_key=idempotency_key,
                context=governed,
            )
        # ponytail: board_assets upload path for tasks with no artifact contract (accepted
        # before acceptance began binding an empty input contract). Drop it, and the
        # protocol-1 submit branch in task_review.py, once no such task remains.
        if idempotency_key is None:
            artifact_uuid = uuid.uuid4()
        else:
            artifact_uuid = uuid.uuid5(
                _IDEMPOTENCY_NAMESPACE, f"{task_id}\x00{signer_id}\x00{idempotency_key}"
            )
            existing = self._store.get_asset(f"asset-{artifact_uuid}", task_id)
            if existing is not None and existing.get("uploader_id") == signer_id:
                return self._asset_response(existing)
        lifecycle_version = await self._check_upload_eligibility(task_id, signer_id)
        remaining_quota = self._remaining_quota_bytes(signer_id, quota)
        if (
            request.headers.get("content-type") is None
            and request.headers.get("content-length") == "0"
        ):
            raise ServiceError("no_file", "No file part in the multipart request", 400, {})
        quota_bounds_file = remaining_quota < self._max_file_size
        effective_limits = AssetUploadLimits(
            max_file_size=min(self._max_file_size, remaining_quota),
            max_request_size=limits.max_request_size,
            max_multipart_overhead_bytes=limits.max_multipart_overhead_bytes,
            stream_chunk_bytes=limits.stream_chunk_bytes,
            max_header_count=limits.max_header_count,
            max_header_size=limits.max_header_size,
            max_boundary_bytes=limits.max_boundary_bytes,
            max_display_filename_utf8_bytes=limits.max_display_filename_utf8_bytes,
        )
        staging_name = f"{artifact_uuid}.upload"
        destination_name = f"{artifact_uuid}.blob"
        root_descriptor = self._borrow_root_descriptor()
        staging_descriptor: int | None = None
        file_descriptor: int | None = None
        live_descriptor: int | None = None
        agent_descriptor: int | None = None
        shard_descriptor: int | None = None
        staging_exists = False
        published = False
        metadata_attempted = False

        try:
            staging_descriptor = _open_or_create_directory(root_descriptor, ".staging")
            os.fsync(root_descriptor)
            file_descriptor = os.open(
                staging_name,
                _FILE_WRITE_FLAGS,
                0o600,
                dir_fd=staging_descriptor,
            )
            staging_exists = True
            os.fsync(staging_descriptor)

            async def write_chunk(chunk: bytes) -> None:
                assert file_descriptor is not None
                _write_all(file_descriptor, chunk)

            try:
                parsed = await stream_asset_upload(
                    request,
                    effective_limits,
                    None,
                    write_chunk,
                )
            except ServiceError as exc:
                if quota_bounds_file and exc.error == "file_too_large":
                    raise _quota_exceeded() from exc
                raise
            os.fsync(file_descriptor)
            os.close(file_descriptor)
            file_descriptor = None

            live_descriptor = _open_or_create_directory(root_descriptor, "live")
            os.fsync(root_descriptor)
            agent_descriptor = _open_or_create_directory(
                live_descriptor, _agent_storage_key(signer_id)
            )
            os.fsync(live_descriptor)
            shard_descriptor = _open_or_create_directory(agent_descriptor, artifact_uuid.hex[:2])
            os.fsync(agent_descriptor)
            os.link(
                staging_name,
                destination_name,
                src_dir_fd=staging_descriptor,
                dst_dir_fd=shard_descriptor,
                follow_symlinks=False,
            )
            published = True
            os.fsync(shard_descriptor)
            _unlink_owned_file(staging_descriptor, staging_name)
            staging_exists = False

            asset = self._asset_metadata(
                artifact_uuid=artifact_uuid,
                task_id=task_id,
                signer_id=signer_id,
                filename=parsed.filename,
                content_type=parsed.content_type,
                size_bytes=parsed.size_bytes,
                content_hash=parsed.content_hash,
            )
            metadata_attempted = True
            self._persist_published_asset(
                asset,
                expected_lifecycle_version=lifecycle_version,
            )
            return self._asset_response(asset)
        except FileExistsError as exc:
            raise ServiceError(
                "artifact_destination_collision",
                "Generated artifact destination already exists",
                409,
                {},
            ) from exc
        except OSError as exc:
            if metadata_attempted:
                raise
            raise ServiceError(
                "storage_unavailable",
                "Artifact storage is unavailable",
                503,
                {},
            ) from exc
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            if published and not metadata_attempted and shard_descriptor is not None:
                with suppress(OSError):
                    _unlink_owned_file(shard_descriptor, destination_name)
            if staging_exists and staging_descriptor is not None:
                with suppress(OSError):
                    _unlink_owned_file(staging_descriptor, staging_name)
            if shard_descriptor is not None:
                os.close(shard_descriptor)
            if agent_descriptor is not None:
                os.close(agent_descriptor)
            if live_descriptor is not None:
                os.close(live_descriptor)
            if staging_descriptor is not None:
                os.close(staging_descriptor)
            os.close(root_descriptor)

    # ------------------------------------------------------------------
    # Governed V6 uploads: stage -> reserve -> install under object_key -> finalize
    # ------------------------------------------------------------------

    async def _check_governed_eligibility(self, task_id: str, signer_id: str) -> int:
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})
        task = await self._deadline_evaluator.evaluate_deadline(task)
        if task["status"] != "accepted":
            raise ServiceError(
                "invalid_status",
                f"Cannot upload assets to task in '{task['status']}' status, must be 'accepted'",
                409,
                {},
            )
        if signer_id != task["worker_id"]:
            raise ServiceError("forbidden", "Only the assigned worker can upload assets", 403, {})
        version = task.get("lifecycle_version")
        if type(version) is not int or not 0 <= version <= (1 << 63) - 1:
            raise ServiceError(
                "invalid_gateway_response", "Gateway returned an invalid response", 502, {}
            )
        return version

    def _governed_artifact(self, task_id: str, artifact_id: str) -> dict[str, Any]:
        artifacts, _policy = self._require_artifacts()
        for item in artifacts.list_task_artifacts(task_id).get("artifacts", []):
            if isinstance(item, dict) and item.get("artifact_id") == artifact_id:
                return item
        raise ServiceError("asset_not_found", "Asset not found", 404, {})

    @staticmethod
    def _artifact_response(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "asset_id": str(item["asset_id"]),
            "task_id": str(item["task_id"]),
            "uploader_id": str(item["uploader_id"]),
            "filename": str(item["filename"]),
            "content_type": str(item["content_type"]),
            "size_bytes": int(item["size_bytes"]),
            "content_hash": str(item["content_hash"]),
            "uploaded_at": str(item["uploaded_at"]),
        }

    async def _stage_governed_body(
        self,
        staging_name: str,
        request: Request,
        limits: AssetUploadLimits,
        quota: AssetStorageQuota,
        signer_id: str,
    ) -> ParsedAssetUpload:
        """Stream the body into ``.staging/``; remove the partial file on any failure."""
        remaining_quota = self._remaining_quota_bytes(signer_id, quota)
        quota_bounds_file = remaining_quota < self._max_file_size
        effective_limits = AssetUploadLimits(
            max_file_size=min(self._max_file_size, remaining_quota),
            max_request_size=limits.max_request_size,
            max_multipart_overhead_bytes=limits.max_multipart_overhead_bytes,
            stream_chunk_bytes=limits.stream_chunk_bytes,
            max_header_count=limits.max_header_count,
            max_header_size=limits.max_header_size,
            max_boundary_bytes=limits.max_boundary_bytes,
            max_display_filename_utf8_bytes=limits.max_display_filename_utf8_bytes,
        )
        root_descriptor = self._borrow_root_descriptor()
        staging_descriptor: int | None = None
        file_descriptor: int | None = None
        completed = False
        try:
            staging_descriptor = _open_or_create_directory(root_descriptor, ".staging")
            file_descriptor = os.open(
                staging_name, _FILE_WRITE_FLAGS, 0o600, dir_fd=staging_descriptor
            )

            async def write_chunk(chunk: bytes) -> None:
                assert file_descriptor is not None
                _write_all(file_descriptor, chunk)

            try:
                parsed = await stream_asset_upload(request, effective_limits, None, write_chunk)
            except ServiceError as exc:
                if quota_bounds_file and exc.error == "file_too_large":
                    raise _quota_exceeded() from exc
                raise
            os.fsync(file_descriptor)
            os.fsync(staging_descriptor)
            completed = True
            return parsed
        except OSError as exc:
            raise ServiceError(
                "storage_unavailable", "Artifact storage is unavailable", 503, {}
            ) from exc
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            if staging_descriptor is not None:
                if not completed:
                    with suppress(OSError):
                        _unlink_owned_file(staging_descriptor, staging_name)
                os.close(staging_descriptor)
            os.close(root_descriptor)

    def _discard_staging(self, staging_name: str) -> None:
        self._delete_storage_key(f".staging/{staging_name}")

    def _install_staged_blob(
        self, staging_name: str, object_key: str, size: int, sha256: str
    ) -> None:
        """Hard-link staged bytes to the reserved object key; adopt an identical blob."""
        parts = _safe_storage_parts(object_key)
        if parts is None or parts[0] != "live":
            raise ServiceError(
                "invalid_gateway_response", "Gateway returned an invalid object key", 502, {}
            )
        root_descriptor = self._borrow_root_descriptor()
        opened: list[int] = []
        staging_descriptor: int | None = None
        try:
            staging_descriptor = os.open(".staging", _DIRECTORY_FLAGS, dir_fd=root_descriptor)
            opened = _open_parent(root_descriptor, parts, create=True)
            parent = opened[-1]
            existing = _file_digest(parent, parts[-1])
            if existing is not None and existing != (size, sha256):
                _unlink_owned_file(parent, parts[-1])
                existing = None
            if existing is None:
                os.link(
                    staging_name,
                    parts[-1],
                    src_dir_fd=staging_descriptor,
                    dst_dir_fd=parent,
                    follow_symlinks=False,
                )
                os.fsync(parent)
            _unlink_owned_file(staging_descriptor, staging_name)
        except OSError as exc:
            raise ServiceError(
                "storage_unavailable", "Artifact storage is unavailable", 503, {}
            ) from exc
        finally:
            for descriptor in reversed(opened):
                os.close(descriptor)
            if staging_descriptor is not None:
                os.close(staging_descriptor)
            os.close(root_descriptor)

    async def _upload_governed(
        self,
        *,
        task_id: str,
        signer_id: str,
        request: Request,
        limits: AssetUploadLimits,
        quota: AssetStorageQuota,
        idempotency_key: str | None,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Bytes follow a durable reservation; a metadata rejection leaves no blob."""
        artifacts, _policy = self._require_artifacts()
        operation_id = _upload_operation_id(task_id, signer_id, idempotency_key)
        if idempotency_key is not None:
            existing = artifacts.get_artifact_upload(operation_id)
            if existing is not None and existing.get("state") == "ready":
                return self._artifact_response(
                    self._governed_artifact(task_id, str(existing["artifact_id"]))
                )
            if existing is not None and existing.get("state") != "reserved":
                raise ServiceError(
                    "upload_abandoned",
                    "This upload was abandoned; retry with a new Idempotency-Key",
                    409,
                    {"state": existing.get("state")},
                )
        lifecycle_version = await self._check_governed_eligibility(task_id, signer_id)
        staging_name = f"{operation_id}.{uuid.uuid4().hex}.upload"
        parsed = await self._stage_governed_body(staging_name, request, limits, quota, signer_id)
        reservation = self._commit_staged(
            operation_id,
            staging_name,
            parsed,
            lambda evidence_format: {
                "protocol_version": 1,
                "upload_operation_id": operation_id,
                "resource": _RESERVE_RESOURCE,
                "task_id": task_id,
                "manifest_id": str(context["submission_manifest_id"]),
                "actor_id": signer_id,
                "actor_role": "worker",
                "display_filename": parsed.filename,
                "evidence_format": evidence_format,
                "declared_size_bytes": parsed.size_bytes,
                "content_sha256": parsed.content_hash,
                "expected_lifecycle_version": lifecycle_version,
                "accepted_binding_kind": str(context["accepted_binding_kind"]),
                "accepted_binding_sha256": str(context["accepted_binding_sha256"]),
            },
        )
        return self._artifact_response(
            self._governed_artifact(task_id, str(reservation["artifact_id"]))
        )

    def _commit_staged(
        self,
        operation_id: str,
        staging_name: str,
        parsed: ParsedAssetUpload,
        build_request: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        """Reserve, install the staged bytes under the object key, then finalize.

        Returns the reservation envelope once the upload is ready (fresh or replayed).
        A rejection before install leaves no blob; a finalize rejection that orphaned
        the upload removes the installed blob; an unknown outcome keeps it for the
        recovery sweep and the caller's same-key retry.
        """
        artifacts, policy = self._require_artifacts()
        try:
            reservation = artifacts.reserve_artifact_upload(
                policy, build_request(_evidence_format(parsed.filename))
            )
        except BaseException:
            self._discard_staging(staging_name)
            raise
        state = reservation.get("state")
        if state != "reserved":
            self._discard_staging(staging_name)
            if state == "ready":
                return reservation
            raise ServiceError("upload_abandoned", "Upload operation is not active", 409, {})
        object_key = str(reservation["object_key"])
        try:
            self._install_staged_blob(
                staging_name, object_key, parsed.size_bytes, parsed.content_hash
            )
        except BaseException:
            self._discard_staging(staging_name)
            raise
        try:
            artifacts.finalize_artifact_upload(
                operation_id,
                policy,
                reservation["attempt"],
                observed_size_bytes=parsed.size_bytes,
                observed_sha256=parsed.content_hash,
            )
        except ServiceError as exc:
            if 400 <= exc.status_code < 500:
                observed = artifacts.get_artifact_upload(operation_id)
                if observed is not None and observed.get("state") == "orphaned":
                    self._delete_storage_key(object_key)
                raise
            raise _finalize_unknown() from exc
        except Exception as exc:
            raise _finalize_unknown() from exc
        return reservation

    def artifact_policy(self) -> dict[str, int]:
        """Return a copy of the configured Gateway artifact policy."""
        _artifacts, policy = self._require_artifacts()
        return dict(policy)

    async def upload_input_artifact(
        self,
        *,
        publication_operation_id: str,
        task_id: str,
        manifest_id: str,
        poster_id: str,
        request: Request,
        limits: AssetUploadLimits,
        quota: AssetStorageQuota,
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        """Stage and commit one poster input before publication (caller authenticated).

        The task row does not exist yet; the Gateway enforces poster ownership, the
        ``awaiting_inputs`` state and the expected input count at reservation.
        """
        if idempotency_key is not None:
            _validate_idempotency_key(idempotency_key)
        artifacts, _policy = self._require_artifacts()
        operation_id = _upload_operation_id(task_id, poster_id, idempotency_key)
        if idempotency_key is not None:
            existing = artifacts.get_artifact_upload(operation_id)
            if existing is not None and existing.get("state") == "ready":
                return {
                    "artifact_id": str(existing["artifact_id"]),
                    "filename": str(existing["display_filename"]),
                    "size_bytes": int(existing["declared_size_bytes"]),
                    "content_hash": str(existing["content_sha256"]),
                    "content_type": str(existing["media_type"]),
                    "role": "input",
                }
            if existing is not None and existing.get("state") != "reserved":
                raise ServiceError(
                    "upload_abandoned",
                    "This upload was abandoned; retry with a new Idempotency-Key",
                    409,
                    {"state": existing.get("state")},
                )
        staging_name = f"{operation_id}.{uuid.uuid4().hex}.upload"
        parsed = await self._stage_governed_body(staging_name, request, limits, quota, poster_id)
        reservation = self._commit_staged(
            operation_id,
            staging_name,
            parsed,
            lambda evidence_format: {
                "protocol_version": 2,
                "upload_operation_id": operation_id,
                "resource": f"POST /task-publications/{publication_operation_id}/inputs",
                "manifest_kind": "input",
                "publication_operation_id": publication_operation_id,
                "task_id": task_id,
                "manifest_id": manifest_id,
                "actor_id": poster_id,
                "actor_role": "poster",
                "display_filename": parsed.filename,
                "evidence_format": evidence_format,
                "declared_size_bytes": parsed.size_bytes,
                "content_sha256": parsed.content_hash,
            },
        )
        return {
            "artifact_id": str(reservation["artifact_id"]),
            "filename": parsed.filename,
            "size_bytes": parsed.size_bytes,
            "content_hash": parsed.content_hash,
            "content_type": _EVIDENCE_MEDIA_TYPES[_evidence_format(parsed.filename)],
            "role": "input",
        }

    # ------------------------------------------------------------------
    # Crash recovery and archive (startup and periodic sweep)
    # ------------------------------------------------------------------

    def _list_storage_files(self, top: str) -> list[tuple[str, float]]:
        """Return (relative key, mtime) of every regular file below one top directory."""
        root_descriptor = self._borrow_root_descriptor()
        try:
            try:
                top_descriptor = os.open(top, _DIRECTORY_FLAGS, dir_fd=root_descriptor)
            except FileNotFoundError:
                return []
            try:
                found: list[tuple[str, float]] = []
                for path, _dirs, files, directory_fd in os.fwalk(
                    ".", dir_fd=top_descriptor, follow_symlinks=False
                ):
                    for name in files:
                        entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        if stat.S_ISREG(entry.st_mode):
                            relative = (Path(top) / path / name).as_posix()
                            found.append((relative, entry.st_mtime))
                return found
            finally:
                os.close(top_descriptor)
        finally:
            os.close(root_descriptor)

    def _blob_digest(self, key: str) -> tuple[int, str] | None:
        parts = _safe_storage_parts(key)
        if parts is None:
            return None
        root_descriptor = self._borrow_root_descriptor()
        try:
            try:
                opened = _open_parent(root_descriptor, parts, create=False)
            except FileNotFoundError:
                return None
            try:
                parent = opened[-1] if opened else root_descriptor
                return _file_digest(parent, parts[-1])
            finally:
                for descriptor in reversed(opened):
                    os.close(descriptor)
        finally:
            os.close(root_descriptor)

    def _copy_blob(self, source_key: str, target_key: str, size: int, sha256: str) -> None:
        """Durably copy verified bytes to the target key (idempotent when already equal)."""
        if self._blob_digest(target_key) == (size, sha256):
            return
        source = self._blob_digest(source_key)
        if source != (size, sha256):
            raise ServiceError(
                "artifact_integrity_blocked", "Stored artifact does not match its metadata", 409, {}
            )
        source_parts = _safe_storage_parts(source_key)
        target_parts = _safe_storage_parts(target_key)
        if source_parts is None or target_parts is None:
            raise ServiceError("invalid_payload", "Invalid storage key", 400, {})
        root_descriptor = self._borrow_root_descriptor()
        source_dirs: list[int] = []
        target_dirs: list[int] = []
        source_fd: int | None = None
        target_fd: int | None = None
        temporary = f"{target_parts[-1]}.{uuid.uuid4().hex}.part"
        try:
            source_dirs = _open_parent(root_descriptor, source_parts, create=False)
            target_dirs = _open_parent(root_descriptor, target_parts, create=True)
            target_parent = target_dirs[-1]
            source_fd = os.open(source_parts[-1], _FILE_READ_FLAGS, dir_fd=source_dirs[-1])
            target_fd = os.open(temporary, _FILE_WRITE_FLAGS, 0o600, dir_fd=target_parent)
            while chunk := os.read(source_fd, 64 * 1024):
                _write_all(target_fd, chunk)
            os.fsync(target_fd)
            os.replace(
                temporary, target_parts[-1], src_dir_fd=target_parent, dst_dir_fd=target_parent
            )
            os.fsync(target_parent)
        except BaseException:
            if target_dirs:
                with suppress(OSError):
                    _unlink_owned_file(target_dirs[-1], temporary)
            raise
        finally:
            if source_fd is not None:
                os.close(source_fd)
            if target_fd is not None:
                os.close(target_fd)
            for descriptor in (*reversed(source_dirs), *reversed(target_dirs)):
                os.close(descriptor)
            os.close(root_descriptor)

    def recover_storage(
        self, *, now_us: int, stale_staging_seconds: float | None
    ) -> dict[str, int]:
        """Reconcile stale reservations and remove unreferenced bytes after a crash.

        ``stale_staging_seconds`` None means startup: no upload is in flight, so every
        ``.staging/`` and unreferenced ``live/`` file is stray. Otherwise only such files
        older than that age go.
        The referenced-key listing is read after the file listing, so a blob installed
        concurrently is always covered by its (earlier) reservation row.
        """
        artifacts, policy = self._require_artifacts()
        counts = {
            "staging_removed": 0,
            "orphans_removed": 0,
            "reservations_finalized": 0,
            "reservations_orphaned": 0,
        }
        staging = self._list_storage_files(".staging")
        live = self._list_storage_files("live")
        recovery = artifacts.get_artifact_recovery()
        for item in recovery.get("reserved", []):
            attempt = item["attempt"]
            if now_us < int(attempt["lease_expires_at_us"]):
                continue
            key = str(item["object_key"])
            expected = (int(item["declared_size_bytes"]), str(item["content_sha256"]))
            if now_us >= int(attempt["absolute_upload_deadline_us"]):
                artifacts.orphan_artifact_upload(
                    str(item["upload_operation_id"]),
                    policy,
                    attempt,
                    failure_code="upload_abandoned",
                )
                self._delete_storage_key(key)
                counts["reservations_orphaned"] += 1
                continue
            if self._blob_digest(key) != expected:
                continue
            canonical = json.loads(str(item["canonical_request_json"]))
            taken = artifacts.reserve_artifact_upload(policy, canonical)
            if taken.get("state") == "reserved":
                artifacts.finalize_artifact_upload(
                    str(item["upload_operation_id"]),
                    policy,
                    taken["attempt"],
                    observed_size_bytes=expected[0],
                    observed_sha256=expected[1],
                )
                counts["reservations_finalized"] += 1
        referenced = set(artifacts.get_artifact_recovery().get("referenced_object_keys", []))
        cutoff = None if stale_staging_seconds is None else time.time() - stale_staging_seconds
        for key, mtime in staging:
            if cutoff is None or mtime < cutoff:
                self._delete_storage_key(key)
                counts["staging_removed"] += 1
        for key, mtime in live:
            # Legacy uploads link bytes before their metadata insert; age-gate them too.
            if key not in referenced and (cutoff is None or mtime < cutoff):
                self._delete_storage_key(key)
                counts["orphans_removed"] += 1
        return counts

    def archive_terminal_artifacts(self, *, archive_after_seconds: int, limit: int) -> int:
        """Copy terminal bytes to ``archive/``, repoint metadata, then drop the live copy."""
        artifacts, _policy = self._require_artifacts()
        archived = 0
        for candidate in artifacts.list_archive_candidates(archive_after_seconds, limit).get(
            "candidates", []
        ):
            live_key = str(candidate["live_object_key"])
            self._copy_blob(
                live_key,
                str(candidate["archive_object_key"]),
                int(candidate["size_bytes"]),
                str(candidate["content_hash"]),
            )
            artifacts.finalize_archive(dict(candidate))
            self._delete_storage_key(live_key)
            archived += 1
        return archived

    async def _authorize_read(
        self,
        task_id: str,
        token: str,
        *,
        action: str,
        resource: str,
        asset_id: str | None,
    ) -> bool:
        """Authenticate a reader; return True for poster, worker, platform or operator.

        Any other validly signed agent is a non-participant: it may read only the
        poster's published input artifacts, and only while the task is nonterminal
        (owner rule: instructions and files are public while the job is open).
        """
        payload, is_platform = await self._token_validator.validate_asset_read_token(
            token, action, expected_resource=resource
        )
        if payload.get("task_id") != task_id or (
            asset_id is not None and payload.get("asset_id") != asset_id
        ):
            raise ServiceError(
                "invalid_payload",
                "Asset token does not match the requested task and asset",
                400,
                {},
            )
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})
        if is_platform:
            return True
        signer_id = payload["_signer_id"]
        if signer_id in (task.get("poster_id"), task.get("worker_id")):
            return True
        if await self._token_validator.is_configured_operator(signer_id):
            return True
        if task.get("status") in _TERMINAL_STATUSES:
            raise _read_forbidden()
        return False

    async def list_assets_for_reader(self, task_id: str, token: str) -> dict[str, Any]:
        """List a task's assets; non-participants see only public input artifacts."""
        participant = await self._authorize_read(
            task_id,
            token,
            action="list_assets",
            resource=f"GET /tasks/{task_id}/assets",
            asset_id=None,
        )
        listing = await self.list_assets(task_id)
        if not participant:
            listing["assets"] = [item for item in listing["assets"] if item["role"] == "input"]
        return listing

    async def download_asset_for_reader(
        self,
        task_id: str,
        asset_id: str,
        token: str,
    ) -> tuple[bytes, str, str]:
        """Download bytes for an authorized reader in every task state."""
        participant = await self._authorize_read(
            task_id,
            token,
            action="download_asset",
            resource=f"GET /tasks/{task_id}/assets/{asset_id}",
            asset_id=asset_id,
        )
        if not participant and not self._is_input_artifact(task_id, asset_id):
            raise _read_forbidden()
        return await self._download_asset(task_id, asset_id, allow_terminal=True)

    def _is_input_artifact(self, task_id: str, asset_id: str) -> bool:
        if self._artifacts is None:
            return False
        return any(
            isinstance(item, dict)
            and item.get("asset_id") == asset_id
            and item.get("manifest_kind") == "input"
            for item in self._artifacts.list_task_artifacts(task_id).get("artifacts", [])
        )

    async def list_assets(self, task_id: str) -> dict[str, Any]:
        """List all assets for a task without authorization (internal callers)."""
        if self._store.get_task(task_id) is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        # ponytail: board_assets rows are the V5 read side (pre-contract tasks); drop
        # this branch once no ungoverned task remains and all posting is protocol 2.
        assets = [
            {
                "asset_id": str(row["asset_id"]),
                "uploader_id": str(row["uploader_id"]),
                "filename": str(row["filename"]),
                "content_type": str(row["content_type"]),
                "size_bytes": int(row["size_bytes"]),
                "content_hash": str(row["content_hash"]),
                "uploaded_at": str(row["uploaded_at"]),
                "role": "output",
            }
            for row in self._store.get_assets_for_task(task_id)
        ]
        if self._artifacts is not None:
            for item in self._artifacts.list_task_artifacts(task_id).get("artifacts", []):
                summary = {
                    key: value
                    for key, value in self._artifact_response(item).items()
                    if key != "task_id"
                }
                summary["role"] = "input" if item.get("manifest_kind") == "input" else "output"
                assets.append(summary)
        return {"task_id": task_id, "assets": assets}

    async def list_manifests_for_reader(self, task_id: str, token: str) -> dict[str, Any]:
        """Return a task's manifests; non-participants see only the input manifest."""
        participant = await self._authorize_read(
            task_id,
            token,
            action="list_assets",
            resource=f"GET /tasks/{task_id}/manifests",
            asset_id=None,
        )
        if self._artifacts is None:
            return {"task_id": task_id, "manifests": []}
        manifests = [
            item
            for item in self._artifacts.list_task_artifacts(task_id).get("manifests", [])
            if participant or item.get("kind") == "input"
        ]
        return {"task_id": task_id, "manifests": manifests}

    async def download_asset(self, task_id: str, asset_id: str) -> tuple[bytes, str, str]:
        """Download public nonterminal bytes through the authoritative storage key."""
        return await self._download_asset(task_id, asset_id, allow_terminal=False)

    async def _download_asset(
        self,
        task_id: str,
        asset_id: str,
        *,
        allow_terminal: bool,
    ) -> tuple[bytes, str, str]:
        """Read one task-bound asset with shared storage and integrity enforcement."""
        root_descriptor = self._borrow_root_descriptor()
        try:
            task = self._store.get_task(task_id)
            if task is None:
                raise ServiceError("task_not_found", "Task not found", 404, {})
            if task["status"] in _TERMINAL_STATUSES and not allow_terminal:
                raise ServiceError(
                    "artifact_unavailable",
                    "Artifact bytes are unavailable for terminal tasks",
                    410,
                    {},
                )

            asset = self._store.get_asset(asset_id, task_id)
            if asset is None and self._artifacts is not None:
                asset = next(
                    (
                        item
                        for item in self._artifacts.list_task_artifacts(task_id).get(
                            "artifacts", []
                        )
                        if isinstance(item, dict) and item.get("asset_id") == asset_id
                    ),
                    None,
                )
            if asset is None:
                raise ServiceError("asset_not_found", "Asset not found", 404, {})

            storage_path = asset.get("storage_path")
            parts = _safe_storage_parts(storage_path) if isinstance(storage_path, str) else None
            if parts is None:
                raise ServiceError("asset_not_found", "Asset file not found on disk", 404, {})

            opened_directories: list[int] = []
            file_descriptor: int | None = None
            parent_descriptor = root_descriptor
            try:
                for component in parts[:-1]:
                    parent_descriptor = os.open(
                        component,
                        _DIRECTORY_FLAGS,
                        dir_fd=parent_descriptor,
                    )
                    opened_directories.append(parent_descriptor)
                file_descriptor = os.open(
                    parts[-1],
                    _FILE_READ_FLAGS,
                    dir_fd=parent_descriptor,
                )
                file_stat = os.fstat(file_descriptor)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise OSError("Asset storage key is not a regular file")
                expected_size = asset.get("size_bytes")
                expected_hash = asset.get("content_hash")
                if (
                    type(expected_size) is not int
                    or not isinstance(expected_hash, str)
                    or file_stat.st_size != expected_size
                ):
                    raise ServiceError(
                        "artifact_integrity_blocked",
                        "Stored artifact does not match its metadata",
                        409,
                        {},
                    )
                file_content = _read_for_integrity_check(file_descriptor, expected_size)
                if (
                    len(file_content) != expected_size
                    or hashlib.sha256(file_content).hexdigest() != expected_hash
                ):
                    raise ServiceError(
                        "artifact_integrity_blocked",
                        "Stored artifact does not match its metadata",
                        409,
                        {},
                    )
            except OSError as exc:
                raise ServiceError(
                    "asset_not_found", "Asset file not found on disk", 404, {}
                ) from exc
            finally:
                if file_descriptor is not None:
                    os.close(file_descriptor)
                for descriptor in reversed(opened_directories):
                    os.close(descriptor)

            return (file_content, str(asset["content_type"]), str(asset["filename"]))
        finally:
            os.close(root_descriptor)

    async def get_asset(self, task_id: str, asset_id: str) -> tuple[bytes, str, str]:
        """Backward-compatible alias for asset download."""
        return await self.download_asset(task_id, asset_id)

    def count_assets(self, task_id: str) -> int:
        """Count assets for a specific task."""
        return self._store.count_assets(task_id)

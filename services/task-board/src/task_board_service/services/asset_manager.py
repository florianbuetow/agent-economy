"""Asset upload/list/download management for task deliverables."""

from __future__ import annotations

import hashlib
import os
import stat
import threading
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

from task_board_service.services.errors import AssetInsertRejectedError
from task_board_service.services.streaming_upload import AssetUploadLimits, stream_asset_upload

if TYPE_CHECKING:
    from starlette.requests import Request

    from task_board_service.services.deadline_evaluator import DeadlineEvaluator
    from task_board_service.services.protocol import TaskStorageInterface
    from task_board_service.services.token_validator import TokenValidator


_CLOSE_ON_EXEC_FLAG = os.O_CLOEXEC if hasattr(os, "O_CLOEXEC") else 0
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | _CLOSE_ON_EXEC_FLAG
_FILE_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | _CLOSE_ON_EXEC_FLAG
_FILE_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | _CLOSE_ON_EXEC_FLAG


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

        lifecycle_version = await self._check_upload_eligibility(task_id, signer_id)
        return signer_id, lifecycle_version

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
            "storage_path": f"live/{artifact_uuid.hex[:2]}/{artifact_uuid}.blob",
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
        shard_descriptor: int | None = None
        file_descriptor: int | None = None
        try:
            live_descriptor = _open_or_create_directory(root_descriptor, "live")
            os.fsync(root_descriptor)
            shard_descriptor = _open_or_create_directory(live_descriptor, artifact_uuid.hex[:2])
            os.fsync(live_descriptor)
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
    ) -> dict[str, Any]:
        """Authenticate first, then stage and atomically publish one streamed asset."""
        signer_id, lifecycle_version = await self._authorize_upload(task_id, token)
        if (
            request.headers.get("content-type") is None
            and request.headers.get("content-length") == "0"
        ):
            raise ServiceError("no_file", "No file part in the multipart request", 400, {})
        effective_limits = AssetUploadLimits(
            max_file_size=self._max_file_size,
            max_request_size=limits.max_request_size,
            max_multipart_overhead_bytes=limits.max_multipart_overhead_bytes,
            stream_chunk_bytes=limits.stream_chunk_bytes,
            max_header_count=limits.max_header_count,
            max_header_size=limits.max_header_size,
            max_boundary_bytes=limits.max_boundary_bytes,
            max_display_filename_utf8_bytes=limits.max_display_filename_utf8_bytes,
        )
        artifact_uuid = uuid.uuid4()
        staging_name = f"{artifact_uuid}.upload"
        destination_name = f"{artifact_uuid}.blob"
        root_descriptor = self._borrow_root_descriptor()
        staging_descriptor: int | None = None
        file_descriptor: int | None = None
        live_descriptor: int | None = None
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

            parsed = await stream_asset_upload(
                request,
                effective_limits,
                None,
                write_chunk,
            )
            os.fsync(file_descriptor)
            os.close(file_descriptor)
            file_descriptor = None

            live_descriptor = _open_or_create_directory(root_descriptor, "live")
            os.fsync(root_descriptor)
            shard_descriptor = _open_or_create_directory(live_descriptor, artifact_uuid.hex[:2])
            os.fsync(live_descriptor)
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
            if live_descriptor is not None:
                os.close(live_descriptor)
            if staging_descriptor is not None:
                os.close(staging_descriptor)
            os.close(root_descriptor)

    async def list_assets(self, task_id: str) -> dict[str, Any]:
        """List all assets for a task. Public and unauthenticated."""
        if self._store.get_task(task_id) is None:
            raise ServiceError("task_not_found", "Task not found", 404, {})

        assets = [
            {
                "asset_id": str(row["asset_id"]),
                "uploader_id": str(row["uploader_id"]),
                "filename": str(row["filename"]),
                "content_type": str(row["content_type"]),
                "size_bytes": int(row["size_bytes"]),
                "content_hash": str(row["content_hash"]),
                "uploaded_at": str(row["uploaded_at"]),
            }
            for row in self._store.get_assets_for_task(task_id)
        ]
        return {"task_id": task_id, "assets": assets}

    async def download_asset(self, task_id: str, asset_id: str) -> tuple[bytes, str, str]:
        """Download an asset through its authoritative relative storage key."""
        root_descriptor = self._borrow_root_descriptor()
        try:
            task = self._store.get_task(task_id)
            if task is None:
                raise ServiceError("task_not_found", "Task not found", 404, {})
            if task["status"] in {"approved", "ruled", "cancelled", "expired"}:
                raise ServiceError(
                    "artifact_unavailable",
                    "Artifact bytes are unavailable for terminal tasks",
                    410,
                    {},
                )

            asset = self._store.get_asset(asset_id, task_id)
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

"""Asset upload/list/download management for task deliverables."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    from task_board_service.services.deadline_evaluator import DeadlineEvaluator
    from task_board_service.services.task_store import TaskStore
    from task_board_service.services.token_validator import TokenValidator


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string with Z suffix."""
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class AssetManager:
    """Manages asset upload, listing, and download for tasks."""

    def __init__(
        self,
        store: TaskStore,
        token_validator: TokenValidator,
        deadline_evaluator: DeadlineEvaluator,
        asset_storage_path: str,
        max_file_size: int,
        max_files_per_task: int,
    ) -> None:
        self._store = store
        self._token_validator = token_validator
        self._deadline_evaluator = deadline_evaluator
        self._asset_storage_path = asset_storage_path
        self._max_file_size = max_file_size
        self._max_files_per_task = max_files_per_task

        # Ensure asset storage directory exists
        Path(self._asset_storage_path).mkdir(parents=True, exist_ok=True)

    async def upload_asset(
        self,
        task_id: str,
        token: str,
        file_content: bytes,
        filename: str,
        content_type: str,
    ) -> dict[str, Any]:
        """
        Upload a deliverable asset.

        Error precedence:
        1-6. JWS verification
        7.   INVALID_PAYLOAD — wrong action, task_id mismatch
        9a.  FORBIDDEN — signer != worker_id in payload
        10.  TASK_NOT_FOUND
        11.  INVALID_STATUS — not ACCEPTED
        9b.  FORBIDDEN — signer != task's worker_id
        12a. FILE_TOO_LARGE
        12b. TOO_MANY_ASSETS
        """
        # Steps 4-7a: Verify JWS, validate action
        payload = await self._token_validator.validate_jws_token(token, "upload_asset")
        signer_id: str = payload["_signer_id"]

        # Step 7b: Validate required fields
        if "task_id" not in payload:
            raise ServiceError("INVALID_PAYLOAD", "Missing required field: task_id", 400, {})

        # Step 7c: task_id in payload must match URL path
        if payload["task_id"] != task_id:
            raise ServiceError(
                "INVALID_PAYLOAD",
                "task_id in payload does not match URL path",
                400,
                {},
            )

        # Step 9a: If present, signer must match worker_id in payload
        if "worker_id" in payload and signer_id != payload["worker_id"]:
            raise ServiceError("FORBIDDEN", "Signer does not match worker_id", 403, {})

        # Step 10: Load task
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        # Evaluate deadline
        task = await self._deadline_evaluator.evaluate_deadline(task)

        # Step 11: Check status (MUST come before role check — see error precedence notes)
        if task["status"] != "accepted":
            raise ServiceError(
                "INVALID_STATUS",
                f"Cannot upload assets to task in '{task['status']}' status, must be 'accepted'",
                409,
                {},
            )

        # Step 9b: Signer must be the task's assigned worker
        if signer_id != task["worker_id"]:
            raise ServiceError(
                "FORBIDDEN",
                "Only the assigned worker can upload assets",
                403,
                {},
            )

        # Step 12a: Check file size
        if len(file_content) > self._max_file_size:
            raise ServiceError(
                "FILE_TOO_LARGE",
                f"File exceeds maximum size of {self._max_file_size} bytes",
                413,
                {},
            )

        # Step 12b: Check asset count
        asset_count = self.count_assets(task_id)
        if asset_count >= self._max_files_per_task:
            raise ServiceError(
                "TOO_MANY_ASSETS",
                f"Maximum of {self._max_files_per_task} assets per task reached",
                409,
                {},
            )

        # Generate asset_id and save file
        asset_id = f"asset-{uuid.uuid4()}"
        uploaded_at = _now_iso()

        # Create directory: {storage_path}/{task_id}/{asset_id}/
        asset_dir = Path(self._asset_storage_path) / task_id / asset_id
        asset_dir.mkdir(parents=True, exist_ok=True)

        # Write file to disk
        file_path = asset_dir / filename
        file_path.write_bytes(file_content)
        content_hash = hashlib.sha256(file_content).hexdigest()

        # Insert asset record
        self._store.insert_asset(
            {
                "asset_id": asset_id,
                "task_id": task_id,
                "uploader_id": signer_id,
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(file_content),
                "content_hash": content_hash,
                "uploaded_at": uploaded_at,
            }
        )

        return {
            "asset_id": asset_id,
            "task_id": task_id,
            "uploader_id": signer_id,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(file_content),
            "content_hash": content_hash,
            "uploaded_at": uploaded_at,
        }

    async def list_assets(self, task_id: str) -> dict[str, Any]:
        """
        List all assets for a task. Public — no authentication.

        Raises:
            ServiceError: TASK_NOT_FOUND
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

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
        """
        Download an asset file.

        Returns (file_content, content_type, filename).

        Raises:
            ServiceError: TASK_NOT_FOUND, ASSET_NOT_FOUND
        """
        task = self._store.get_task(task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "Task not found", 404, {})

        asset = self._store.get_asset(asset_id, task_id)
        if asset is None:
            raise ServiceError("ASSET_NOT_FOUND", "Asset not found", 404, {})

        filename = str(asset["filename"])
        content_type = str(asset["content_type"])

        # Resolve the file path safely — prevent path traversal
        asset_dir = Path(self._asset_storage_path) / task_id / asset_id
        file_path = (asset_dir / filename).resolve()

        # Ensure the resolved path is within the asset storage directory
        storage_root = Path(self._asset_storage_path).resolve()
        if not str(file_path).startswith(str(storage_root)):
            raise ServiceError("ASSET_NOT_FOUND", "Asset not found", 404, {})

        if not file_path.exists():
            raise ServiceError("ASSET_NOT_FOUND", "Asset file not found on disk", 404, {})

        file_content = file_path.read_bytes()
        return (file_content, content_type, filename)

    async def get_asset(self, task_id: str, asset_id: str) -> tuple[bytes, str, str]:
        """Backward-compatible alias for asset download."""
        return await self.download_asset(task_id, asset_id)

    def count_assets(self, task_id: str) -> int:
        """Count assets for a specific task."""
        return self._store.count_assets(task_id)

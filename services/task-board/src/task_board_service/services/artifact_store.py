"""Gateway artifact operations Task Board needs for governed V6 uploads.

The Gateway records reservations, attempts, ready/archived state and manifests;
Task Board owns the bytes under the asset storage root. ``TaskDbClient``
implements this protocol; a store that does not (legacy fakes) keeps every task
on the V5 ``board_assets`` path.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ArtifactStore(Protocol):
    """Typed Gateway artifact calls; every mutating call carries the explicit policy."""

    def get_artifact_context(self, task_id: str) -> dict[str, Any]:
        """Return governance, binding and submission-manifest facts for one task."""
        ...

    def reserve_artifact_upload(
        self, policy: dict[str, int], request: dict[str, Any]
    ) -> dict[str, Any]:
        """Reserve, replay or take over one upload operation."""
        ...

    def finalize_artifact_upload(
        self,
        upload_operation_id: str,
        policy: dict[str, int],
        fence: dict[str, Any],
        *,
        observed_size_bytes: int,
        observed_sha256: str,
    ) -> dict[str, Any]:
        """Mark the exact observed bytes ready under the attempt fence."""
        ...

    def orphan_artifact_upload(
        self,
        upload_operation_id: str,
        policy: dict[str, int],
        fence: dict[str, Any],
        *,
        failure_code: str,
    ) -> dict[str, Any]:
        """Durably abandon the current attempt."""
        ...

    def get_artifact_upload(self, upload_operation_id: str) -> dict[str, Any] | None:
        """Return one upload's durable state, or None when unknown."""
        ...

    def list_task_artifacts(self, task_id: str) -> dict[str, Any]:
        """Return ready/archived artifacts and manifests of one task."""
        ...

    def get_artifact_recovery(self) -> dict[str, Any]:
        """Return reserved-upload fences and every storage key that must be kept."""
        ...

    def list_archive_candidates(self, archive_after_seconds: int, limit: int) -> dict[str, Any]:
        """Return live bytes of tasks terminal for longer than the configured age."""
        ...

    def finalize_archive(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Point one artifact at its already-written archive copy."""
        ...


__all__ = ["ArtifactStore"]

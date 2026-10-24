"""Atomic native V6 submission-manifest validation and freezing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from service_commons.exceptions import ServiceError

from db_gateway_service.services.artifact_manifest import (
    UPLOAD_COLUMNS,
    canonical_manifest,
    validate_upload_row,
)
from db_gateway_service.services.legacy_artifact_continuation import (
    validate_legacy_continuation,
)

if TYPE_CHECKING:
    import sqlite3

    from db_gateway_service.task_operation_requests import TaskOperationRequest


@dataclass(frozen=True, slots=True)
class FrozenSubmissionManifest:
    """Identity returned after a native submission manifest is frozen."""

    manifest_id: str
    manifest_sha256: str


def _error(code: str, message: str, status: int) -> ServiceError:
    return ServiceError(code, message, status, {})


def reject_governed_protocol_v1_submission(
    cursor: sqlite3.Cursor,
    task_id: str,
) -> None:
    """Keep V1 only for an explicit pre-cutover task without artifact governance."""
    artifact_tables = {
        str(row[0])
        for row in cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('board_task_artifact_contracts','board_artifact_cutover')"
        ).fetchall()
    }
    if not artifact_tables:
        return
    if artifact_tables != {"board_task_artifact_contracts", "board_artifact_cutover"}:
        raise _error(
            "invalid_payload",
            "Artifact governance is incomplete; protocol version 1 is unavailable",
            400,
        )
    governed = cursor.execute(
        "SELECT 1 FROM board_task_artifact_contracts WHERE task_id=?",
        (task_id,),
    ).fetchone()
    cutover = cursor.execute("SELECT 1 FROM board_artifact_cutover LIMIT 1").fetchone()
    if governed is not None or cutover is not None:
        raise _error(
            "invalid_payload",
            "Protocol version 2 is required for governed artifact submission",
            400,
        )


def freeze_submission_manifest(
    cursor: sqlite3.Cursor,
    request: TaskOperationRequest,
    now_us: int,
) -> FrozenSubmissionManifest:
    """Validate governed authority and freeze the exact sorted ready-artifact set."""
    manifest_id = str(request.argument("manifest_id"))
    worker_id = str(request.argument("worker_id"))
    binding_kind = str(request.argument("accepted_binding_kind"))
    binding_sha256 = str(request.argument("accepted_binding_sha256"))

    contract = cursor.execute(
        "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
        "accepted_publication_contract_sha256,legacy_acceptance_observation_sha256,"
        "legacy_acceptance_observation_status FROM board_task_artifact_contracts "
        "WHERE task_id=?",
        (request.task_id,),
    ).fetchone()
    if contract is None:
        raise _error("artifact_contract_missing", "Task artifact contract is missing", 409)
    if contract[0] == 0:
        validate_legacy_continuation(
            cursor,
            task_id=request.task_id,
            worker_id=worker_id,
            expected_lifecycle_version=request.expected_lifecycle_version,
            accepted_binding_kind=binding_kind,
            accepted_binding_sha256=binding_sha256,
        )
    else:
        selected_hash = contract[3] if str(contract[2]) == "publication_contract" else contract[4]
        if (
            contract[0] != 1
            or contract[1] != "native_v6"
            or contract[2] != binding_kind
            or selected_hash is None
            or selected_hash != binding_sha256
        ):
            raise _error("accepted_binding_mismatch", "Accepted artifact binding differs", 409)
        if binding_kind != "publication_contract":
            raise _error("accepted_binding_mismatch", "Accepted artifact binding differs", 409)

    manifest = cursor.execute(
        "SELECT task_id,kind,actor_id,format_version,state FROM board_artifact_manifests "
        "WHERE manifest_id=?",
        (manifest_id,),
    ).fetchone()
    if manifest is None:
        raise _error("manifest_not_found", "Submission manifest not found", 404)
    if (
        str(manifest[0]) != request.task_id
        or str(manifest[1]) != "submission"
        or str(manifest[2]) != worker_id
        or int(manifest[3]) != 1
        or str(manifest[4]) != "draft"
    ):
        raise _error("manifest_mismatch", "Submission manifest is not writable", 409)

    active = cursor.execute(
        "SELECT 1 FROM board_artifact_uploads WHERE manifest_id=? AND state='reserved' LIMIT 1",
        (manifest_id,),
    ).fetchone()
    if active is not None:
        raise _error("upload_pending", "Submission manifest has an active upload", 409)
    rows = cursor.execute(
        f"SELECT {UPLOAD_COLUMNS} FROM board_artifact_uploads "  # nosec B608
        "WHERE manifest_id=? AND task_id=? AND actor_id=? AND state='ready' "
        "AND accepted_binding_kind=? AND accepted_binding_sha256=? "
        "ORDER BY artifact_id",
        (manifest_id, request.task_id, worker_id, binding_kind, binding_sha256),
    ).fetchall()
    if not rows:
        raise _error("no_assets", "At least one ready artifact is required", 400)
    uploads = [tuple(row) for row in rows]
    for upload in uploads:
        validate_upload_row(upload, expected_caller_kid=None)
    canonical, manifest_sha256 = canonical_manifest(
        manifest_id=manifest_id,
        task_id=request.task_id,
        kind="submission",
        actor_id=worker_id,
        uploads=uploads,
    )
    try:
        cursor.execute(
            "UPDATE board_artifact_manifests SET state='frozen',canonical_manifest_json=?,"
            "manifest_sha256=?,frozen_at_us=? WHERE manifest_id=? AND state='draft'",
            (canonical, manifest_sha256, now_us, manifest_id),
        )
    except RuntimeError as exc:
        raise _error(
            "task_operation_persistence_failed",
            "Task operation could not be persisted atomically",
            500,
        ) from exc
    if cursor.rowcount != 1:
        raise _error("manifest_mismatch", "Submission manifest changed during freeze", 409)
    return FrozenSubmissionManifest(manifest_id, manifest_sha256)


__all__ = [
    "FrozenSubmissionManifest",
    "freeze_submission_manifest",
    "reject_governed_protocol_v1_submission",
]

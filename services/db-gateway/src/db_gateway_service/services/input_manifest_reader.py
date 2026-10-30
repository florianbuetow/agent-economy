"""Read-only projection of a published task's frozen input manifest (G03)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from service_commons.exceptions import ServiceError

if TYPE_CHECKING:
    import sqlite3


def _corrupt() -> ServiceError:
    return ServiceError(
        "artifact_state_corrupt",
        "Persisted input manifest failed integrity validation",
        500,
        {},
    )


def read_task_input_manifest(connection: sqlite3.Connection, task_id: str) -> dict[str, Any]:
    """Return the task's bound input manifest, or ``None`` for tasks without one.

    Raises ``task_not_found`` when the task does not exist.
    """
    task = connection.execute("SELECT 1 FROM board_tasks WHERE task_id=?", (task_id,)).fetchone()
    if task is None:
        raise ServiceError("task_not_found", "Task not found", 404, {})
    contract = connection.execute(
        "SELECT input_manifest_id,input_manifest_sha256,publication_contract_sha256,"
        "accepted_binding_kind,accepted_publication_contract_sha256 "
        "FROM board_task_artifact_contracts WHERE task_id=? AND input_manifest_id IS NOT NULL",
        (task_id,),
    ).fetchone()
    if contract is None:
        return {"task_id": task_id, "input_manifest": None}
    manifest = connection.execute(
        "SELECT manifest_id,actor_id,state,manifest_sha256,frozen_at_us "
        "FROM board_artifact_manifests WHERE manifest_id=? AND kind='input'",
        (str(contract[0]),),
    ).fetchone()
    if manifest is None or str(manifest[3]) != str(contract[1]):
        raise _corrupt()
    rows = connection.execute(
        "SELECT artifact_id,display_filename,declared_size_bytes,content_sha256,media_type,"
        "object_key,evidence_format,finalized_at_us FROM board_artifact_uploads "
        "WHERE manifest_id=? AND manifest_kind='input' AND state IN ('ready','archived') "
        "ORDER BY artifact_id",
        (str(manifest[0]),),
    ).fetchall()
    files = [
        {
            "artifact_id": str(row[0]),
            "filename": str(row[1]),
            "size_bytes": int(row[2]),
            "content_sha256": str(row[3]),
            "content_type": str(row[4]),
            "object_key": str(row[5]),
            "evidence_format": str(row[6]),
            "role": "input",
            "uploader_id": str(manifest[1]),
        }
        for row in rows
    ]
    return {
        "task_id": task_id,
        "input_manifest": {
            "manifest_id": str(manifest[0]),
            "actor_id": str(manifest[1]),
            "state": str(manifest[2]),
            "manifest_sha256": str(manifest[3]),
            "frozen_at_us": None if manifest[4] is None else int(manifest[4]),
            "publication_contract_sha256": str(contract[2]),
            "accepted_binding_kind": None if contract[3] is None else str(contract[3]),
            "accepted_publication_contract_sha256": (
                None if contract[4] is None else str(contract[4])
            ),
            "files": files,
        },
    }


__all__ = ["read_task_input_manifest"]

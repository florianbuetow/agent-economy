"""Runtime wiring of V6 artifact uploads, manifests, crash recovery and archive.

Task Board is the only caller. It supplies the explicit artifact policy on every
mutating request (the Gateway holds no artifact defaults), and owns the bytes:
the Gateway only records reservations, attempts, ready/archived state and the
authoritative object key each byte blob must live under.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any, Final

from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import (
    ArtifactUploadRequest,
    parse_artifact_policy,
    parse_scoped_artifact_upload_request,
)
from db_gateway_service.services.artifact_writer import ArtifactWriter
from db_gateway_service.task_time import epoch_us_to_utc_text, parse_utc_text_to_epoch_us

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable, Mapping

_SUBMISSION_MANIFEST_PREFIX: Final[str] = "manifest-submission-"
_TERMINAL_TIME_COLUMN: Final[dict[str, str]] = {
    "approved": "approved_at",
    "ruled": "ruled_at",
    "cancelled": "cancelled_at",
    "expired": "expired_at",
}
_ARCHIVE_OWNER: Final[str] = "task-board-archive"
_MICROSECONDS_PER_SECOND: Final[int] = 1_000_000
_HASH_LENGTH: Final[int] = 64


def _error(code: str, message: str, status: int) -> ServiceError:
    return ServiceError(code, message, status, {})


def submission_manifest_id(task_id: str) -> str:
    """Return the single deterministic submission manifest id of a task."""
    return f"{_SUBMISSION_MANIFEST_PREFIX}{task_id}"


def archive_object_key(live_object_key: str) -> str:
    """Map a ``live/`` storage key to its archive location."""
    if not live_object_key.startswith("live/"):
        raise _error("invalid_payload", "Only live objects can be archived", 400)
    return "archive/" + live_object_key.removeprefix("live/")


def _artifact_tables_present(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN "
        "('board_task_artifact_contracts','board_artifact_manifests','board_artifact_uploads',"
        "'board_artifact_archive_attempts')"
    ).fetchone()
    return row is not None and int(row[0]) == 4


def task_artifact_context(connection: sqlite3.Connection, task_id: str) -> dict[str, object]:
    """Report whether a task is artifact-governed and the facts an upload must bind."""
    task = connection.execute(
        "SELECT board_tasks.status, board_tasks.worker_id, board_task_contracts.lifecycle_version "
        "FROM board_tasks LEFT JOIN board_task_contracts USING(task_id) "
        "WHERE board_tasks.task_id=?",
        (task_id,),
    ).fetchone()
    if task is None:
        raise _error("task_not_found", "Task not found", 404)
    context: dict[str, object] = {
        "task_id": task_id,
        "governed": False,
        "status": str(task[0]),
        "worker_id": None if task[1] is None else str(task[1]),
        "lifecycle_version": None if task[2] is None else int(task[2]),
        "accepted_binding_kind": None,
        "accepted_binding_sha256": None,
        "submission_manifest_id": None,
        "submission_manifest_state": None,
    }
    if not _artifact_tables_present(connection):
        return context
    contract = connection.execute(
        "SELECT accepted_binding_kind, accepted_publication_contract_sha256, "
        "legacy_acceptance_observation_sha256 FROM board_task_artifact_contracts "
        "WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if contract is None or contract[0] is None:
        return context
    kind = str(contract[0])
    selected = contract[1] if kind == "publication_contract" else contract[2]
    manifest_id = submission_manifest_id(task_id)
    manifest = connection.execute(
        "SELECT state FROM board_artifact_manifests WHERE manifest_id=?",
        (manifest_id,),
    ).fetchone()
    context.update(
        {
            "governed": True,
            "accepted_binding_kind": kind,
            "accepted_binding_sha256": None if selected is None else str(selected),
            "submission_manifest_id": manifest_id,
            "submission_manifest_state": None if manifest is None else str(manifest[0]),
        }
    )
    return context


def _ensure_submission_manifest(
    connection: sqlite3.Connection,
    request: ArtifactUploadRequest,
) -> None:
    """Create the draft submission manifest for the assigned worker exactly once."""
    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        existing = cursor.execute(
            "SELECT 1 FROM board_artifact_manifests WHERE manifest_id=?",
            (request.manifest_id,),
        ).fetchone()
        if existing is None:
            task = cursor.execute(
                "SELECT status, worker_id FROM board_tasks WHERE task_id=?",
                (request.task_id,),
            ).fetchone()
            if task is None:
                raise _error("task_not_found", "Task not found", 404)
            if str(task[0]) != "accepted":
                raise _error("invalid_status", "Task does not accept worker uploads", 409)
            if request.actor_role != "worker" or task[1] != request.actor_id:
                raise _error("forbidden", "Only the assigned worker may upload", 403)
            contract = cursor.execute(
                "SELECT accepted_binding_kind FROM board_task_artifact_contracts WHERE task_id=?",
                (request.task_id,),
            ).fetchone()
            if contract is None or contract[0] is None:
                raise _error("artifact_contract_missing", "Task artifact contract is missing", 409)
            cursor.execute(
                "INSERT INTO board_artifact_manifests (manifest_id,task_id,kind,actor_id,"
                "format_version,state,canonical_manifest_json,manifest_sha256,frozen_at_us,"
                "publication_operation_id,public_task_id) "
                "VALUES (?,?,'submission',?,1,'draft',NULL,NULL,NULL,NULL,?)",
                (request.manifest_id, request.task_id, request.actor_id, request.task_id),
            )
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _policy(body: Mapping[str, object]) -> Any:
    if "policy" not in body:
        raise _error("invalid_artifact_policy", "Artifact policy is required", 400)
    return parse_artifact_policy(body["policy"])


def _writer(
    connection: sqlite3.Connection,
    body: Mapping[str, object],
    clock_us: Callable[[], int],
    current_task_board_caller_kid: str,
) -> ArtifactWriter:
    return ArtifactWriter(
        connection,
        clock_us=clock_us,
        policy=_policy(body),
        current_task_board_caller_kid=current_task_board_caller_kid,
    )


def reserve_artifact_upload(
    connection: sqlite3.Connection,
    body: Mapping[str, object],
    *,
    caller_kid: str,
    clock_us: Callable[[], int],
    current_task_board_caller_kid: str,
) -> dict[str, Any]:
    """Replay, take over, or reserve one upload; bytes may follow only a reservation."""
    writer = _writer(connection, body, clock_us, current_task_board_caller_kid)
    if "request" not in body:
        raise _error("invalid_payload", "Artifact upload request is required", 400)
    request = parse_scoped_artifact_upload_request(body["request"])
    if isinstance(request, ArtifactUploadRequest):
        if request.manifest_id != submission_manifest_id(request.task_id):
            raise _error("manifest_mismatch", "Submission manifest id is not the task's", 409)
        known = connection.execute(
            "SELECT 1 FROM board_artifact_uploads WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        if known is None:
            _ensure_submission_manifest(connection, request)
    return writer.reserve_upload(request, verified_caller_kid=caller_kid)


def _fence(body: Mapping[str, object]) -> tuple[str, int, str]:
    owner_id = body.get("owner_id")
    generation = body.get("generation")
    nonce = body.get("attempt_nonce")
    if not isinstance(owner_id, str) or type(generation) is not int or not isinstance(nonce, str):
        raise _error("invalid_payload", "Upload attempt fence is required", 400)
    return owner_id, generation, nonce


def finalize_artifact_upload(
    connection: sqlite3.Connection,
    upload_operation_id: str,
    body: Mapping[str, object],
    *,
    caller_kid: str,
    clock_us: Callable[[], int],
    current_task_board_caller_kid: str,
) -> dict[str, Any]:
    """Mark the exact observed bytes ready under the current attempt fence."""
    writer = _writer(connection, body, clock_us, current_task_board_caller_kid)
    owner_id, generation, nonce = _fence(body)
    size = body.get("observed_size_bytes")
    sha256 = body.get("observed_sha256")
    if type(size) is not int or not isinstance(sha256, str):
        raise _error("invalid_payload", "Observed size and hash are required", 400)
    return writer.finalize_upload(
        upload_operation_id,
        owner_id=owner_id,
        generation=generation,
        attempt_nonce=nonce,
        observed_size_bytes=size,
        observed_sha256=sha256,
        verified_caller_kid=caller_kid,
    )


def renew_artifact_upload(
    connection: sqlite3.Connection,
    upload_operation_id: str,
    body: Mapping[str, object],
    *,
    caller_kid: str,
    clock_us: Callable[[], int],
    current_task_board_caller_kid: str,
) -> dict[str, Any]:
    """Extend the current attempt lease up to its absolute deadline."""
    writer = _writer(connection, body, clock_us, current_task_board_caller_kid)
    owner_id, generation, nonce = _fence(body)
    return writer.renew_upload(
        upload_operation_id,
        owner_id=owner_id,
        generation=generation,
        attempt_nonce=nonce,
        verified_caller_kid=caller_kid,
    )


def orphan_artifact_upload(
    connection: sqlite3.Connection,
    upload_operation_id: str,
    body: Mapping[str, object],
    *,
    caller_kid: str,
    clock_us: Callable[[], int],
    current_task_board_caller_kid: str,
) -> dict[str, Any]:
    """Durably abandon the current attempt; its bytes must then be removed."""
    writer = _writer(connection, body, clock_us, current_task_board_caller_kid)
    owner_id, generation, nonce = _fence(body)
    failure_code = body.get("failure_code")
    if not isinstance(failure_code, str) or not failure_code:
        raise _error("invalid_payload", "Failure code is required", 400)
    return writer.orphan_upload(
        upload_operation_id,
        owner_id=owner_id,
        generation=generation,
        attempt_nonce=nonce,
        failure_code=failure_code,
        verified_caller_kid=caller_kid,
    )


def get_artifact_upload(
    connection: sqlite3.Connection,
    upload_operation_id: str,
    *,
    caller_kid: str,
) -> dict[str, object]:
    """Return one caller-owned upload's durable state (no attempt secrets)."""
    row = connection.execute(
        "SELECT upload_operation_id,state,artifact_id,object_key,task_id,actor_id,"
        "display_filename,media_type,declared_size_bytes,content_sha256,manifest_kind,"
        "finalized_at_us,caller_kid FROM board_artifact_uploads WHERE upload_operation_id=?",
        (upload_operation_id,),
    ).fetchone()
    if row is None or str(row[12]) != caller_kid:
        raise _error("upload_not_found", "Upload operation not found", 404)
    return {
        "upload_operation_id": str(row[0]),
        "state": str(row[1]),
        "artifact_id": str(row[2]),
        "object_key": str(row[3]),
        "task_id": str(row[4]),
        "actor_id": str(row[5]),
        "display_filename": str(row[6]),
        "media_type": str(row[7]),
        "declared_size_bytes": int(row[8]),
        "content_sha256": str(row[9]),
        "manifest_kind": str(row[10]),
        "finalized_at_us": None if row[11] is None else int(row[11]),
    }


def _archived_key(connection: sqlite3.Connection, artifact_id: str) -> str | None:
    row = connection.execute(
        "SELECT archive_object_key FROM board_artifact_archive_attempts "
        "WHERE artifact_id=? AND state='finalized'",
        (artifact_id,),
    ).fetchone()
    return None if row is None else str(row[0])


def list_task_artifacts(connection: sqlite3.Connection, task_id: str) -> dict[str, object]:
    """List a task's ready/archived artifacts and its manifests with frozen bytes."""
    if (
        connection.execute("SELECT 1 FROM board_tasks WHERE task_id=?", (task_id,)).fetchone()
        is None
    ):
        raise _error("task_not_found", "Task not found", 404)
    if not _artifact_tables_present(connection):
        return {"task_id": task_id, "artifacts": [], "manifests": []}
    rows = connection.execute(
        "SELECT artifact_id,actor_id,display_filename,media_type,declared_size_bytes,"
        "content_sha256,finalized_at_us,object_key,state,manifest_kind,manifest_id "
        "FROM board_artifact_uploads WHERE task_id=? AND state IN ('ready','archived') "
        "ORDER BY finalized_at_us, artifact_id",
        (task_id,),
    ).fetchall()
    artifacts: list[dict[str, object]] = []
    for row in rows:
        artifact_id = str(row[0])
        state = str(row[8])
        storage_path = str(row[7])
        if state == "archived":
            archived = _archived_key(connection, artifact_id)
            if archived is None:
                raise _error("artifact_state_corrupt", "Archived artifact has no archive key", 500)
            storage_path = archived
        artifacts.append(
            {
                "asset_id": f"asset-{artifact_id}",
                "artifact_id": artifact_id,
                "task_id": task_id,
                "uploader_id": str(row[1]),
                "filename": str(row[2]),
                "content_type": str(row[3]),
                "size_bytes": int(row[4]),
                "content_hash": str(row[5]),
                "uploaded_at": epoch_us_to_utc_text(int(row[6])),
                "storage_path": storage_path,
                "state": state,
                "manifest_kind": str(row[9]),
                "manifest_id": str(row[10]),
            }
        )
    manifests = [
        {
            "manifest_id": str(row[0]),
            "kind": str(row[1]),
            "actor_id": str(row[2]),
            "state": str(row[3]),
            "manifest_sha256": None if row[4] is None else str(row[4]),
            "canonical_manifest_json": None if row[5] is None else str(row[5]),
        }
        for row in connection.execute(
            "SELECT manifest_id,kind,actor_id,state,manifest_sha256,canonical_manifest_json "
            "FROM board_artifact_manifests WHERE task_id=? ORDER BY kind, manifest_id",
            (task_id,),
        ).fetchall()
    ]
    return {"task_id": task_id, "artifacts": artifacts, "manifests": manifests}


def artifact_recovery_listing(
    connection: sqlite3.Connection,
    *,
    caller_kid: str,
) -> dict[str, object]:
    """Expose stale-reservation fences and every storage key that must be kept."""
    referenced: set[str] = {
        str(row[0])
        for row in connection.execute("SELECT storage_path FROM board_assets").fetchall()
    }
    reserved: list[dict[str, object]] = []
    if _artifact_tables_present(connection):
        referenced.update(
            str(row[0])
            for row in connection.execute(
                "SELECT object_key FROM board_artifact_uploads WHERE state IN ('reserved','ready')"
            ).fetchall()
        )
        referenced.update(
            str(row[0])
            for row in connection.execute(
                "SELECT archive_object_key FROM board_artifact_archive_attempts"
            ).fetchall()
        )
        for row in connection.execute(
            "SELECT u.upload_operation_id,u.object_key,u.declared_size_bytes,u.content_sha256,"
            "u.canonical_request_json,a.owner_id,a.generation,a.attempt_nonce,"
            "a.lease_expires_at_us,a.absolute_upload_deadline_us "
            "FROM board_artifact_uploads u JOIN board_artifact_upload_attempts a "
            "ON a.attempt_id=u.current_attempt_id "
            "WHERE u.state='reserved' AND u.caller_kid=? ORDER BY u.upload_operation_id",
            (caller_kid,),
        ).fetchall():
            reserved.append(
                {
                    "upload_operation_id": str(row[0]),
                    "object_key": str(row[1]),
                    "declared_size_bytes": int(row[2]),
                    "content_sha256": str(row[3]),
                    "canonical_request_json": str(row[4]),
                    "attempt": {
                        "owner_id": str(row[5]),
                        "generation": int(row[6]),
                        "attempt_nonce": str(row[7]),
                        "lease_expires_at_us": int(row[8]),
                        "absolute_upload_deadline_us": int(row[9]),
                    },
                }
            )
    return {"reserved": reserved, "referenced_object_keys": sorted(referenced)}


def _terminal_since_us(connection: sqlite3.Connection, task_id: str) -> int | None:
    row = connection.execute(
        "SELECT status,approved_at,ruled_at,cancelled_at,expired_at FROM board_tasks "
        "WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    column = _TERMINAL_TIME_COLUMN.get(str(row[0]))
    if column is None:
        return None
    value = row[1 + tuple(_TERMINAL_TIME_COLUMN.values()).index(column)]
    if value is None:
        return None
    return parse_utc_text_to_epoch_us(value)


def archive_candidates(
    connection: sqlite3.Connection,
    *,
    archive_after_seconds: int,
    limit: int,
    now_us: int,
) -> dict[str, object]:
    """List live bytes of tasks terminal for strictly more than the configured age."""
    if type(archive_after_seconds) is not int or archive_after_seconds < 1:
        raise _error("invalid_payload", "archive_after_seconds must be a positive integer", 400)
    if type(limit) is not int or limit < 1:
        raise _error("invalid_payload", "limit must be a positive integer", 400)
    after_us = archive_after_seconds * _MICROSECONDS_PER_SECOND
    eligible: list[str] = []
    for row in connection.execute(
        "SELECT task_id FROM board_tasks WHERE status IN ('approved','ruled','cancelled',"
        "'expired') ORDER BY task_id"
    ).fetchall():
        since = _terminal_since_us(connection, str(row[0]))
        if since is not None and now_us - since > after_us:
            eligible.append(str(row[0]))
    candidates: list[dict[str, object]] = []
    governed = _artifact_tables_present(connection)
    for task_id in eligible:
        if governed:
            for row in connection.execute(
                "SELECT artifact_id,object_key,declared_size_bytes,content_sha256 "
                "FROM board_artifact_uploads WHERE task_id=? AND state='ready' "
                "ORDER BY artifact_id",
                (task_id,),
            ).fetchall():
                candidates.append(
                    {
                        "kind": "artifact",
                        "id": str(row[0]),
                        "task_id": task_id,
                        "live_object_key": str(row[1]),
                        "archive_object_key": archive_object_key(str(row[1])),
                        "size_bytes": int(row[2]),
                        "content_hash": str(row[3]),
                    }
                )
        for row in connection.execute(
            "SELECT asset_id,storage_path,size_bytes,content_hash FROM board_assets "
            "WHERE task_id=? AND storage_path LIKE 'live/%' ORDER BY asset_id",
            (task_id,),
        ).fetchall():
            candidates.append(
                {
                    "kind": "asset",
                    "id": str(row[0]),
                    "task_id": task_id,
                    "live_object_key": str(row[1]),
                    "archive_object_key": archive_object_key(str(row[1])),
                    "size_bytes": int(row[2]),
                    "content_hash": None if row[3] is None else str(row[3]),
                }
            )
        if len(candidates) >= limit:
            break
    return {"candidates": candidates[:limit]}


def _candidate_fields(candidate: Mapping[str, object]) -> tuple[str, str, str, str, int, str]:
    kind = candidate.get("kind")
    item_id = candidate.get("id")
    live = candidate.get("live_object_key")
    archive = candidate.get("archive_object_key")
    size = candidate.get("size_bytes")
    sha256 = candidate.get("content_hash")
    if (
        kind not in {"artifact", "asset"}
        or not isinstance(item_id, str)
        or not item_id
        or not isinstance(live, str)
        or not isinstance(archive, str)
        or type(size) is not int
        or not isinstance(sha256, str)
        or len(sha256) != _HASH_LENGTH
    ):
        raise _error("invalid_payload", "Archive candidate is invalid", 400)
    if archive != archive_object_key(live):
        raise _error("invalid_payload", "Archive key does not match the live key", 400)
    return str(kind), item_id, live, archive, size, sha256


def _archive_artifact(
    cursor: sqlite3.Cursor,
    fields: tuple[str, str, str, str, int, str],
    clock_us: Callable[[], int],
) -> bool:
    """Return True when already archived; otherwise record the archive atomically."""
    _kind, item_id, live, archive, size, sha256 = fields
    connection = cursor.connection
    row = cursor.execute(
        "SELECT state,object_key,declared_size_bytes,content_sha256,task_id "
        "FROM board_artifact_uploads WHERE artifact_id=?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise _error("asset_not_found", "Artifact not found", 404)
    if str(row[0]) == "archived" and _archived_key(connection, item_id) == archive:
        return True
    if str(row[0]) != "ready" or str(row[1]) != live or int(row[2]) != size or row[3] != sha256:
        raise _error("archive_conflict", "Artifact no longer matches the candidate", 409)
    if _terminal_since_us(connection, str(row[4])) is None:
        raise _error("invalid_status", "Only terminal task artifacts are archived", 409)
    now_us = clock_us()
    cursor.execute(
        "INSERT INTO board_artifact_archive_attempts (archive_operation_id,artifact_id,"
        "owner_id,attempt_nonce,live_object_key,archive_object_key,expected_size_bytes,"
        "expected_sha256,state,created_at_us,updated_at_us) "
        "VALUES (?,?,?,?,?,?,?,?,'finalized',?,?)",
        (
            f"archive-{item_id}",
            item_id,
            _ARCHIVE_OWNER,
            hashlib.sha256(item_id.encode()).hexdigest()[:32],
            live,
            archive,
            size,
            sha256,
            now_us,
            now_us,
        ),
    )
    cursor.execute(
        "UPDATE board_artifact_uploads SET state='archived' WHERE artifact_id=? AND state='ready'",
        (item_id,),
    )
    if cursor.rowcount != 1:
        raise _error("archive_conflict", "Artifact changed during archive", 409)
    return False


def _archive_asset(cursor: sqlite3.Cursor, fields: tuple[str, str, str, str, int, str]) -> bool:
    """Return True when already archived; otherwise repoint the legacy asset."""
    _kind, item_id, live, archive, size, sha256 = fields
    row = cursor.execute(
        "SELECT storage_path,size_bytes,content_hash,task_id FROM board_assets WHERE asset_id=?",
        (item_id,),
    ).fetchone()
    if row is None:
        raise _error("asset_not_found", "Asset not found", 404)
    if str(row[0]) == archive:
        return True
    if str(row[0]) != live or int(row[1]) != size or row[2] != sha256:
        raise _error("archive_conflict", "Asset no longer matches the candidate", 409)
    if _terminal_since_us(cursor.connection, str(row[3])) is None:
        raise _error("invalid_status", "Only terminal task assets are archived", 409)
    cursor.execute(
        "UPDATE board_assets SET storage_path=? WHERE asset_id=? AND storage_path=?",
        (archive, item_id, live),
    )
    if cursor.rowcount != 1:
        raise _error("archive_conflict", "Asset changed during archive", 409)
    return False


def finalize_archive(
    connection: sqlite3.Connection,
    candidate: Mapping[str, object],
    *,
    clock_us: Callable[[], int],
) -> dict[str, object]:
    """Point an artifact at its already-written archive copy; never deletes metadata."""
    fields = _candidate_fields(candidate)
    kind, item_id, _live, archive, _size, _sha256 = fields
    cursor = connection.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        if kind == "artifact":
            _archive_artifact(cursor, fields, clock_us)
        else:
            _archive_asset(cursor, fields)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return {"kind": kind, "id": item_id, "storage_path": archive, "state": "archived"}


__all__ = [
    "archive_candidates",
    "archive_object_key",
    "artifact_recovery_listing",
    "finalize_archive",
    "finalize_artifact_upload",
    "get_artifact_upload",
    "list_task_artifacts",
    "orphan_artifact_upload",
    "renew_artifact_upload",
    "reserve_artifact_upload",
    "submission_manifest_id",
    "task_artifact_context",
]

"""Atomic V6 artifact reservation and upload-attempt fencing."""

from __future__ import annotations

import secrets
import sqlite3
from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import (
    ArtifactPolicy,
    InputArtifactUploadRequestV2,
    ScopedArtifactUploadRequest,
)
from db_gateway_service.schema import SQLITE_MAX_INTEGER
from db_gateway_service.services.artifact_manifest import (
    MEDIA_TYPES,
    validate_attempt_for_upload,
    validate_upload_row,
)
from db_gateway_service.services.legacy_artifact_continuation import (
    validate_legacy_continuation,
)
from db_gateway_service.services.prepublication_integrity import assert_publication_integrity

if TYPE_CHECKING:
    from collections.abc import Callable

_MICROSECONDS_PER_SECOND: Final[int] = 1_000_000


def _error(
    code: str,
    message: str,
    status: int,
) -> ServiceError:
    return ServiceError(code, message, status, {})


class ArtifactWriter:
    """Own atomic artifact-upload mutations on one supplied SQLite connection."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock_us: Callable[[], int],
        policy: ArtifactPolicy,
        current_task_board_caller_kid: str,
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        if not callable(clock_us):
            raise TypeError("clock_us must be callable")
        if not isinstance(policy, ArtifactPolicy):
            raise TypeError("policy must be an ArtifactPolicy")
        if not isinstance(current_task_board_caller_kid, str) or not current_task_board_caller_kid:
            raise ValueError("current_task_board_caller_kid must be a nonempty string")
        self._db = connection
        self._clock_us = clock_us
        self._policy = policy
        self._current_task_board_caller_kid = current_task_board_caller_kid

    def reserve_upload(
        self,
        request: ScopedArtifactUploadRequest,
        *,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        """Replay, take over, or atomically reserve one upload operation."""
        self._authorize(verified_caller_kid)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            cursor.execute("PRAGMA defer_foreign_keys=ON")
            existing = self._upload(cursor, request.upload_operation_id)
            if existing is not None:
                validate_upload_row(existing, expected_caller_kid=verified_caller_kid)
                replay_result = self._replay_or_takeover(
                    cursor,
                    existing,
                    request,
                    verified_caller_kid,
                )
                if replay_result[0]:
                    self._db.commit()
                else:
                    self._db.rollback()
                return replay_result[1]

            now_us = self._clock()
            execution_cutoff = self._validate_reservation_eligibility(
                cursor,
                request,
                now_us,
                verified_caller_kid,
            )
            self._validate_new_reservation_quota(
                cursor,
                request,
                verified_caller_kid,
            )
            duration = self._seconds_to_microseconds(
                self._policy.max_upload_duration_seconds,
                "invalid_artifact_policy",
            )
            configured_deadline = self._checked_add(now_us, duration, "invalid_clock")
            absolute_deadline = (
                configured_deadline
                if execution_cutoff is None
                else min(configured_deadline, execution_cutoff)
            )
            created = self._create_upload(
                cursor,
                request,
                now_us,
                verified_caller_kid,
                absolute_deadline,
            )
            self._db.commit()
            return created
        except ServiceError:
            self._db.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise _error(
                "artifact_persistence_failed",
                "Artifact reservation could not be persisted atomically",
                500,
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def renew_upload(
        self,
        upload_operation_id: str,
        *,
        owner_id: str,
        generation: int,
        attempt_nonce: str,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        """Extend only the current unexpired lease up to its absolute deadline."""
        self._authorize(verified_caller_kid)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            _upload, attempt = self._owned_attempt(
                cursor,
                upload_operation_id,
                owner_id,
                generation,
                attempt_nonce,
                verified_caller_kid,
            )
            now_us = self._clock()
            if now_us >= int(attempt[8]):
                raise _error("upload_deadline_exceeded", "Upload deadline has passed", 409)
            if now_us >= int(attempt[7]):
                raise _error("upload_lease_expired", "Upload lease has expired", 409)
            lease_delta = self._seconds_to_microseconds(
                self._policy.reservation_ttl_seconds,
                "invalid_artifact_policy",
            )
            candidate = self._checked_add(now_us, lease_delta, "invalid_clock")
            new_expiry = max(min(candidate, int(attempt[8])), int(attempt[7]))
            cursor.execute(
                "UPDATE board_artifact_upload_attempts SET lease_expires_at_us=?, updated_at_us=? "
                "WHERE attempt_id=?",
                (new_expiry, now_us, str(attempt[0])),
            )
            self._require_one(cursor, "Upload renewal was not persisted")
            refreshed = self._attempt(cursor, str(attempt[0]))
            if refreshed is None:
                raise _error("artifact_state_corrupt", "Renewed upload attempt is missing", 500)
            self._db.commit()
            return self._attempt_envelope(refreshed)
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise

    def finalize_upload(
        self,
        upload_operation_id: str,
        *,
        owner_id: str,
        generation: int,
        attempt_nonce: str,
        observed_size_bytes: int,
        observed_sha256: str,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        """Finalize exact observed bytes, fencing lifecycle-losing attempts as orphaned."""
        self._authorize(verified_caller_kid)
        self._validate_observed(observed_size_bytes, observed_sha256)
        cursor = self._db.cursor()
        durable_error: ServiceError | None = None
        try:
            cursor.execute("BEGIN IMMEDIATE")
            upload, attempt = self._owned_attempt(
                cursor,
                upload_operation_id,
                owner_id,
                generation,
                attempt_nonce,
                verified_caller_kid,
            )
            now_us = self._clock()
            if now_us >= int(attempt[8]):
                raise _error("upload_deadline_exceeded", "Upload deadline has passed", 409)
            if now_us >= int(attempt[7]):
                raise _error("upload_lease_expired", "Upload lease has expired", 409)
            if observed_size_bytes != int(upload[12]) or observed_sha256 != str(upload[13]):
                raise _error("upload_content_mismatch", "Observed upload bytes do not match", 409)

            eligibility_error = self._finalize_eligibility_error(
                cursor,
                upload,
                verified_caller_kid,
                now_us,
            )
            if eligibility_error is not None:
                self._persist_orphan(
                    cursor,
                    upload,
                    attempt,
                    now_us,
                    eligibility_error.error,
                )
                self._db.commit()
                durable_error = eligibility_error
            else:
                cursor.execute(
                    "UPDATE board_artifact_upload_attempts SET state='finalized', "
                    "observed_size_bytes=?, observed_sha256=?, failure_code=NULL, updated_at_us=? "
                    "WHERE attempt_id=?",
                    (observed_size_bytes, observed_sha256, now_us, str(attempt[0])),
                )
                self._require_one(cursor, "Upload attempt finalization was not persisted")
                cursor.execute(
                    "UPDATE board_artifact_uploads SET state='ready', current_attempt_id=NULL, "
                    "finalized_at_us=? WHERE upload_operation_id=?",
                    (now_us, str(upload[0])),
                )
                self._require_one(cursor, "Upload finalization was not persisted")
                refreshed = self._upload(cursor, str(upload[0]))
                if refreshed is None:
                    raise _error("artifact_state_corrupt", "Finalized upload is missing", 500)
                result = self._upload_envelope(refreshed, None)
                self._db.commit()
                return result
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise
        raise durable_error

    def orphan_upload(
        self,
        upload_operation_id: str,
        *,
        owner_id: str,
        generation: int,
        attempt_nonce: str,
        failure_code: str,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        """Durably orphan the exact current attempt."""
        self._authorize(verified_caller_kid)
        if not isinstance(failure_code, str) or not failure_code:
            raise _error("invalid_payload", "Failure code is required", 400)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            upload, attempt = self._owned_attempt(
                cursor,
                upload_operation_id,
                owner_id,
                generation,
                attempt_nonce,
                verified_caller_kid,
            )
            now_us = self._clock()
            self._persist_orphan(cursor, upload, attempt, now_us, failure_code)
            refreshed = self._upload(cursor, str(upload[0]))
            if refreshed is None:
                raise _error("artifact_state_corrupt", "Orphaned upload is missing", 500)
            result = self._upload_envelope(refreshed, None)
            self._db.commit()
            return result
        except ServiceError:
            self._db.rollback()
            raise
        except Exception:
            self._db.rollback()
            raise

    def _validate_reservation_eligibility(
        self,
        cursor: sqlite3.Cursor,
        request: ScopedArtifactUploadRequest,
        now_us: int,
        verified_caller_kid: str,
    ) -> int | None:
        if isinstance(request, InputArtifactUploadRequestV2):
            self._validate_input_eligibility(
                cursor,
                request,
                verified_caller_kid,
            )
            return None
        task = self._task(cursor, request.task_id)
        if int(task[2]) != request.expected_lifecycle_version:
            raise _error("task_version_conflict", "Task lifecycle version changed", 409)
        if str(task[1]) != "accepted":
            raise _error("invalid_status", "Task does not accept worker uploads", 409)
        if request.actor_role != "worker" or str(task[3]) != request.actor_id:
            raise _error("forbidden", "Only the assigned worker may upload", 403)
        manifest = cursor.execute(
            "SELECT task_id,kind,actor_id,state FROM board_artifact_manifests WHERE manifest_id=?",
            (request.manifest_id,),
        ).fetchone()
        if manifest is None:
            raise _error("manifest_not_found", "Artifact manifest not found", 404)
        if (
            str(manifest[0]) != request.task_id
            or str(manifest[1]) != "submission"
            or str(manifest[2]) != request.actor_id
            or str(manifest[3]) != "draft"
        ):
            raise _error("manifest_mismatch", "Artifact manifest is not writable", 409)
        contract = cursor.execute(
            "SELECT accepted_binding_kind, accepted_publication_contract_sha256, "
            "legacy_acceptance_observation_sha256 "
            "FROM board_task_artifact_contracts "
            "WHERE task_id=?",
            (request.task_id,),
        ).fetchone()
        if contract is None:
            raise _error("artifact_contract_missing", "Task artifact contract is missing", 409)
        selected = contract[1] if str(contract[0]) == "publication_contract" else contract[2]
        if (
            str(contract[0]) != request.accepted_binding_kind
            or selected is None
            or str(selected) != request.accepted_binding_sha256
        ):
            raise _error("accepted_binding_mismatch", "Accepted artifact binding differs", 409)
        if request.accepted_binding_kind == "legacy_accepted_continuation":
            validate_legacy_continuation(
                cursor,
                task_id=request.task_id,
                worker_id=request.actor_id,
                expected_lifecycle_version=request.expected_lifecycle_version,
                accepted_binding_kind=request.accepted_binding_kind,
                accepted_binding_sha256=request.accepted_binding_sha256,
            )
        execution_cutoff = task[4]
        if execution_cutoff is None or int(execution_cutoff) <= now_us:
            raise _error("upload_deadline_exceeded", "Task execution cutoff has passed", 409)
        return int(execution_cutoff)

    def _validate_input_eligibility(
        self,
        cursor: sqlite3.Cursor,
        request: InputArtifactUploadRequestV2,
        verified_caller_kid: str,
    ) -> int:
        publication = self._input_publication(cursor, request.publication_operation_id)
        if publication is None:
            raise _error("publication_not_found", "Publication not found", 404)
        if str(publication[5]) != verified_caller_kid:
            raise _error("artifact_caller_mismatch", "Publication belongs to another caller", 403)
        if str(publication[2]) != request.task_id:
            raise _error("publication_mismatch", "Task does not match publication", 409)
        if str(publication[6]) != request.actor_id:
            raise _error("forbidden", "Only the publication poster may upload inputs", 403)
        if str(publication[8]) != "awaiting_inputs":
            raise _error("invalid_status", "Publication is not accepting inputs", 409)
        assert_publication_integrity(cursor, publication)
        manifest = cursor.execute(
            "SELECT task_id,kind,actor_id,state,publication_operation_id "
            "FROM board_artifact_manifests WHERE manifest_id=?",
            (request.manifest_id,),
        ).fetchone()
        if manifest is None:
            raise _error("manifest_not_found", "Artifact manifest not found", 404)
        if (
            str(manifest[0]) != request.task_id
            or str(manifest[1]) != "input"
            or str(manifest[2]) != request.actor_id
            or str(manifest[3]) != "draft"
            or str(manifest[4]) != request.publication_operation_id
        ):
            raise _error("manifest_mismatch", "Input manifest does not match publication", 409)
        return int(publication[18])

    def _validate_new_reservation_quota(
        self,
        cursor: sqlite3.Cursor,
        request: ScopedArtifactUploadRequest,
        verified_caller_kid: str,
    ) -> None:
        if request.declared_size_bytes > self._policy.max_file_bytes:
            raise _error("file_too_large", "Declared artifact exceeds the file limit", 413)
        counts = cursor.execute(
            "SELECT COUNT(*), COALESCE(SUM(declared_size_bytes),0) "
            "FROM board_artifact_uploads WHERE manifest_id=? AND state IN ('reserved','ready')",
            (request.manifest_id,),
        ).fetchone()
        if counts is None:
            raise _error("artifact_state_corrupt", "Manifest quota could not be read", 500)
        if isinstance(request, InputArtifactUploadRequestV2):
            expected_count = self._validate_input_eligibility(
                cursor,
                request,
                verified_caller_kid,
            )
            if int(counts[0]) >= expected_count:
                raise _error("input_count_exceeded", "Expected input count is already filled", 409)
        if int(counts[0]) >= self._policy.max_files_per_manifest:
            raise _error("too_many_assets", "Manifest file limit reached", 409)
        if int(counts[1]) > self._policy.max_manifest_bytes - request.declared_size_bytes:
            raise _error("manifest_bytes_exceeded", "Manifest byte limit reached", 409)

    def _create_upload(
        self,
        cursor: sqlite3.Cursor,
        request: ScopedArtifactUploadRequest,
        now_us: int,
        caller_kid: str,
        absolute_deadline: int,
    ) -> dict[str, Any]:
        artifact_id = str(uuid4())
        object_key = f"live/{artifact_id[:2]}/{artifact_id}.blob"
        attempt = self._new_attempt_values(
            request.upload_operation_id,
            generation=1,
            object_key=object_key,
            now_us=now_us,
            absolute_deadline=absolute_deadline,
        )
        cursor.execute(
            "INSERT INTO board_artifact_uploads (upload_operation_id,request_sha256,"
            "canonical_request_json,caller_kid,resource,task_id,manifest_id,actor_id,actor_role,"
            "display_filename,evidence_format,media_type,declared_size_bytes,content_sha256,"
            "expected_lifecycle_version,accepted_binding_kind,accepted_binding_sha256,"
            "manifest_kind,publication_operation_id,state,"
            "artifact_id,object_key,current_attempt_id,created_at_us,finalized_at_us) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                request.upload_operation_id,
                request.request_sha256,
                request.canonical_json,
                caller_kid,
                request.resource,
                request.task_id,
                request.manifest_id,
                request.actor_id,
                request.actor_role,
                request.display_filename,
                request.evidence_format,
                MEDIA_TYPES[request.evidence_format],
                request.declared_size_bytes,
                request.content_sha256,
                None
                if isinstance(request, InputArtifactUploadRequestV2)
                else request.expected_lifecycle_version,
                None
                if isinstance(request, InputArtifactUploadRequestV2)
                else request.accepted_binding_kind,
                None
                if isinstance(request, InputArtifactUploadRequestV2)
                else request.accepted_binding_sha256,
                "input" if isinstance(request, InputArtifactUploadRequestV2) else "submission",
                request.publication_operation_id
                if isinstance(request, InputArtifactUploadRequestV2)
                else None,
                "reserved",
                artifact_id,
                object_key,
                attempt[0],
                now_us,
            ),
        )
        self._insert_attempt(cursor, request.upload_operation_id, object_key, attempt)
        upload = self._upload(cursor, request.upload_operation_id)
        persisted_attempt = self._attempt(cursor, str(attempt[0]))
        if upload is None or persisted_attempt is None:
            raise _error("artifact_state_corrupt", "Reserved upload was not persisted", 500)
        return self._upload_envelope(upload, persisted_attempt)

    def _replay_or_takeover(
        self,
        cursor: sqlite3.Cursor,
        upload: tuple[Any, ...],
        request: ScopedArtifactUploadRequest,
        verified_caller_kid: str,
    ) -> tuple[bool, dict[str, Any]]:
        if str(upload[1]) != request.request_sha256:
            raise _error("upload_operation_conflict", "Upload operation has different intent", 409)
        state = str(upload[17])
        if state != "reserved":
            return False, self._upload_envelope(upload, None)
        attempt_id = upload[20]
        if not isinstance(attempt_id, str):
            raise _error("artifact_state_corrupt", "Reserved upload has no current attempt", 500)
        attempt = self._attempt(cursor, attempt_id)
        if attempt is None:
            raise _error("artifact_state_corrupt", "Current upload attempt is missing", 500)
        validate_attempt_for_upload(upload, attempt)
        now_us = self._clock()
        if now_us >= int(attempt[8]):
            raise _error("upload_deadline_exceeded", "Upload deadline has passed", 409)
        if now_us < int(attempt[7]):
            raise _error("upload_pending", "Upload operation is already active", 409)
        self._validate_reservation_eligibility(
            cursor,
            request,
            now_us,
            verified_caller_kid,
        )
        generation = int(attempt[3])
        if generation >= SQLITE_MAX_INTEGER:
            raise _error("upload_generation_exhausted", "Upload generation cannot advance", 409)
        cursor.execute(
            "UPDATE board_artifact_upload_attempts SET state='expired', updated_at_us=? "
            "WHERE attempt_id=?",
            (now_us, attempt_id),
        )
        self._require_one(cursor, "Expired upload attempt was not persisted")
        replacement = self._new_attempt_values(
            str(upload[0]),
            generation=generation + 1,
            object_key=str(upload[19]),
            now_us=now_us,
            absolute_deadline=int(attempt[8]),
        )
        cursor.execute(
            "UPDATE board_artifact_uploads SET current_attempt_id=? WHERE upload_operation_id=?",
            (replacement[0], str(upload[0])),
        )
        self._require_one(cursor, "Upload takeover ownership was not persisted")
        self._insert_attempt(cursor, str(upload[0]), str(upload[19]), replacement)
        refreshed = self._upload(cursor, str(upload[0]))
        persisted = self._attempt(cursor, str(replacement[0]))
        if refreshed is None or persisted is None:
            raise _error("artifact_state_corrupt", "Takeover attempt was not persisted", 500)
        return True, self._upload_envelope(refreshed, persisted)

    def _new_attempt_values(
        self,
        operation_id: str,
        *,
        generation: int,
        object_key: str,
        now_us: int,
        absolute_deadline: int,
    ) -> tuple[object, ...]:
        attempt_id = str(uuid4())
        owner_id = str(uuid4())
        nonce = secrets.token_hex(16)
        lease_delta = self._seconds_to_microseconds(
            self._policy.reservation_ttl_seconds,
            "invalid_artifact_policy",
        )
        lease_expiry = min(
            self._checked_add(now_us, lease_delta, "invalid_clock"),
            absolute_deadline,
        )
        staging_key = f"staging/{operation_id}.{generation}.{nonce}.part"
        return (
            attempt_id,
            owner_id,
            generation,
            nonce,
            staging_key,
            object_key,
            lease_expiry,
            absolute_deadline,
            now_us,
        )

    @staticmethod
    def _insert_attempt(
        cursor: sqlite3.Cursor,
        operation_id: str,
        object_key: str,
        values: tuple[object, ...],
    ) -> None:
        cursor.execute(
            "INSERT INTO board_artifact_upload_attempts (attempt_id,upload_operation_id,"
            "owner_id,generation,attempt_nonce,staging_key,intended_object_key,"
            "lease_expires_at_us,absolute_upload_deadline_us,state,observed_size_bytes,"
            "observed_sha256,failure_code,created_at_us,updated_at_us) "
            "VALUES (?,?,?,?,?,?,?,?,?,'active',NULL,NULL,NULL,?,?)",
            (
                values[0],
                operation_id,
                values[1],
                values[2],
                values[3],
                values[4],
                object_key,
                values[6],
                values[7],
                values[8],
                values[8],
            ),
        )

    def _owned_attempt(
        self,
        cursor: sqlite3.Cursor,
        operation_id: object,
        owner_id: object,
        generation: object,
        attempt_nonce: object,
        verified_caller_kid: str,
    ) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
        if not isinstance(operation_id, str) or not operation_id:
            raise _error("invalid_payload", "Upload operation ID is required", 400)
        upload = self._upload(cursor, operation_id)
        if upload is None:
            raise _error("upload_not_found", "Upload operation not found", 404)
        validate_upload_row(upload, expected_caller_kid=verified_caller_kid)
        attempt_id = upload[20]
        if str(upload[17]) != "reserved" or not isinstance(attempt_id, str):
            raise _error("stale_upload_attempt", "Upload attempt no longer owns the operation", 409)
        attempt = self._attempt(cursor, attempt_id)
        if attempt is None:
            raise _error("artifact_state_corrupt", "Current upload attempt is missing", 500)
        validate_attempt_for_upload(upload, attempt)
        if (
            not isinstance(owner_id, str)
            or str(attempt[2]) != owner_id
            or type(generation) is not int
            or int(attempt[3]) != generation
            or not isinstance(attempt_nonce, str)
            or str(attempt[4]) != attempt_nonce
        ):
            raise _error("stale_upload_attempt", "Upload attempt fence does not match", 409)
        return upload, attempt

    def _persist_orphan(
        self,
        cursor: sqlite3.Cursor,
        upload: tuple[Any, ...],
        attempt: tuple[Any, ...],
        now_us: int,
        failure_code: str,
    ) -> None:
        cursor.execute(
            "UPDATE board_artifact_upload_attempts SET state='orphaned', "
            "observed_size_bytes=NULL, observed_sha256=NULL, failure_code=?, updated_at_us=? "
            "WHERE attempt_id=?",
            (failure_code, now_us, str(attempt[0])),
        )
        self._require_one(cursor, "Upload attempt orphaning was not persisted")
        cursor.execute(
            "UPDATE board_artifact_uploads SET state='orphaned', current_attempt_id=NULL, "
            "finalized_at_us=? WHERE upload_operation_id=?",
            (now_us, str(upload[0])),
        )
        self._require_one(cursor, "Upload orphaning was not persisted")

    @staticmethod
    def _upload(cursor: sqlite3.Cursor, operation_id: str) -> tuple[Any, ...] | None:
        row = cursor.execute(
            "SELECT upload_operation_id,request_sha256,canonical_request_json,caller_kid,"
            "resource,task_id,manifest_id,actor_id,actor_role,display_filename,evidence_format,"
            "media_type,declared_size_bytes,content_sha256,expected_lifecycle_version,"
            "accepted_binding_kind,accepted_binding_sha256,state,artifact_id,object_key,"
            "current_attempt_id,created_at_us,finalized_at_us,manifest_kind,"
            "publication_operation_id FROM board_artifact_uploads "
            "WHERE upload_operation_id=?",
            (operation_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    @staticmethod
    def _attempt(cursor: sqlite3.Cursor, attempt_id: str) -> tuple[Any, ...] | None:
        row = cursor.execute(
            "SELECT attempt_id,upload_operation_id,owner_id,generation,attempt_nonce,"
            "staging_key,intended_object_key,lease_expires_at_us,absolute_upload_deadline_us,"
            "state,observed_size_bytes,observed_sha256,failure_code,created_at_us,updated_at_us "
            "FROM board_artifact_upload_attempts WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    @staticmethod
    def _task(cursor: sqlite3.Cursor, task_id: str) -> tuple[Any, ...]:
        row = cursor.execute(
            "SELECT board_tasks.task_id,board_tasks.status,board_task_contracts.lifecycle_version,"
            "board_tasks.worker_id,board_task_contracts.execution_cutoff_us FROM board_tasks "
            "JOIN board_task_contracts USING(task_id) WHERE board_tasks.task_id=?",
            (task_id,),
        ).fetchone()
        if row is None:
            raise _error("task_not_found", "Task not found", 404)
        return tuple(row)

    @staticmethod
    def _input_publication(cursor: sqlite3.Cursor, operation_id: str) -> tuple[Any, ...] | None:
        row = cursor.execute(
            "SELECT operation_id,publication_contract_version,task_id,canonical_request_json,"
            "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
            "escrow_id,lock_request_sha256,lock_receipt_event_id,published_at_us,"
            "creation_event_id,result_json,error_json,request_protocol_version,"
            "expected_input_count,base_request_sha256,freeze_operation_id,"
            "canonical_freeze_request_json,freeze_request_sha256,input_manifest_sha256,"
            "publication_contract_sha256,inputs_frozen_at_us,publish_operation_id,"
            "canonical_publish_request_json,publish_request_sha256,publish_authorized_at_us "
            "FROM board_task_publications WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        return None if row is None else tuple(row)

    def _finalize_eligibility_error(
        self,
        cursor: sqlite3.Cursor,
        upload: tuple[Any, ...],
        verified_caller_kid: str,
        now_us: int,
    ) -> ServiceError | None:
        request = validate_upload_row(upload, expected_caller_kid=verified_caller_kid)
        try:
            if isinstance(request, InputArtifactUploadRequestV2):
                self._validate_input_eligibility(cursor, request, verified_caller_kid)
            elif request.accepted_binding_kind == "legacy_accepted_continuation":
                self._validate_reservation_eligibility(
                    cursor,
                    request,
                    now_us,
                    verified_caller_kid,
                )
            else:
                task = self._task(cursor, str(upload[5]))
                if int(task[2]) != int(upload[14]):
                    raise _error(
                        "task_version_conflict",
                        "Task lifecycle changed during upload",
                        409,
                    )
                if str(task[1]) != "accepted":
                    raise _error("invalid_status", "Task does not accept worker uploads", 409)
        except ServiceError as exc:
            return exc
        return None

    @staticmethod
    def _attempt_envelope(attempt: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "attempt_id": str(attempt[0]),
            "owner_id": str(attempt[2]),
            "generation": int(attempt[3]),
            "attempt_nonce": str(attempt[4]),
            "staging_key": str(attempt[5]),
            "lease_expires_at_us": int(attempt[7]),
            "absolute_upload_deadline_us": int(attempt[8]),
            "state": str(attempt[9]),
        }

    @classmethod
    def _upload_envelope(
        cls,
        upload: tuple[Any, ...],
        attempt: tuple[Any, ...] | None,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "upload_operation_id": str(upload[0]),
            "state": str(upload[17]),
            "artifact_id": str(upload[18]),
            "object_key": str(upload[19]),
            "accepted_binding_kind": None if upload[15] is None else str(upload[15]),
            "accepted_binding_sha256": None if upload[16] is None else str(upload[16]),
            "declared_size_bytes": int(upload[12]),
            "content_sha256": str(upload[13]),
            "manifest_kind": str(upload[23]),
            "publication_operation_id": None if upload[24] is None else str(upload[24]),
        }
        if attempt is not None:
            result["attempt"] = cls._attempt_envelope(attempt)
        return result

    def _authorize(self, value: object) -> None:
        if not isinstance(value, str) or not value:
            raise _error("artifact_caller_mismatch", "Verified Task Board caller is required", 403)
        if value != self._current_task_board_caller_kid:
            raise _error("artifact_caller_mismatch", "Gateway caller is not authorized", 403)

    @staticmethod
    def _require_one(cursor: sqlite3.Cursor, message: str) -> None:
        if cursor.rowcount != 1:
            raise _error("artifact_persistence_failed", message, 500)

    def _clock(self) -> int:
        value = self._clock_us()
        if type(value) is not int or not 0 <= value <= SQLITE_MAX_INTEGER:
            raise _error("invalid_clock", "Artifact clock returned an invalid timestamp", 500)
        return value

    @staticmethod
    def _seconds_to_microseconds(seconds: int, code: str) -> int:
        if seconds > SQLITE_MAX_INTEGER // _MICROSECONDS_PER_SECOND:
            raise _error(code, "Configured artifact duration exceeds SQLite range", 500)
        return seconds * _MICROSECONDS_PER_SECOND

    @staticmethod
    def _checked_add(left: int, right: int, code: str) -> int:
        if left > SQLITE_MAX_INTEGER - right:
            raise _error(code, "Artifact timestamp exceeds SQLite range", 500)
        return left + right

    @staticmethod
    def _validate_observed(size: object, sha256: object) -> None:
        if type(size) is not int or not 0 <= size <= SQLITE_MAX_INTEGER:
            raise _error("invalid_payload", "Observed artifact size is invalid", 400)
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise _error("invalid_payload", "Observed artifact hash is invalid", 400)

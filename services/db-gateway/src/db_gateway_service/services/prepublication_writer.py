"""Atomic zero-input V2 prepublication ownership journal."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import ArtifactPolicy
from db_gateway_service.services.artifact_manifest import (
    UPLOAD_COLUMNS,
    canonical_manifest,
    validate_upload_row,
)
from db_gateway_service.services.prepublication_integrity import assert_publication_integrity
from db_gateway_service.services.prepublication_receipt import (
    deadline_chain,
    fixed_utc,
    inspect_v2_lock,
    materialize_publication,
)
from db_gateway_service.task_operation_requests import parse_operation_id
from db_gateway_service.task_publication_financial import PublicationEvidenceError
from db_gateway_service.task_publication_v2_requests import parse_publication_admission_v2
from db_gateway_service.task_time import epoch_us_to_utc_text

if TYPE_CHECKING:
    from collections.abc import Callable

    from db_gateway_service.task_publication_v2_requests import (
        InputManifestFreezeV2,
        PublicationAdmissionV2,
        PublicationPublishV2,
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _error(
    code: str,
    message: str,
    status: int,
) -> ServiceError:
    return ServiceError(code, message, status, {})


class PrepublicationWriter:
    """Own V2 admission, empty-input freeze and publish authorization."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        clock_us: Callable[[], int],
        artifact_policy: ArtifactPolicy,
        current_task_board_caller_kid: str,
    ) -> None:
        if not callable(clock_us):
            raise TypeError("clock_us must be callable")
        if not isinstance(artifact_policy, ArtifactPolicy):
            raise TypeError("artifact_policy must be an ArtifactPolicy")
        if not isinstance(current_task_board_caller_kid, str) or not current_task_board_caller_kid:
            raise ValueError("current_task_board_caller_kid must be a nonempty string")
        self._db = connection
        self._clock_us = clock_us
        self._policy = artifact_policy
        self._current_task_board_caller_kid = current_task_board_caller_kid

    def admit(
        self,
        request: PublicationAdmissionV2,
        *,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        self._authorize_caller(verified_caller_kid)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            existing = self._publication(cursor, request.publication_operation_id)
            if existing is not None:
                self._assert_stored_caller(existing, verified_caller_kid)
                assert_publication_integrity(cursor, existing)
                result = self._replay_admission(cursor, existing, request)
                self._db.rollback()
                return result
            self._assert_identity_available(
                cursor,
                request.publication_operation_id,
                request.lock_operation_id,
            )
            self._assert_task_available(cursor, request.task_id)
            if request.expected_input_count > self._policy.max_files_per_manifest:
                raise _error(
                    "invalid_payload",
                    "expected_input_count exceeds the configured manifest limit",
                    400,
                )
            if (
                cursor.execute(
                    "SELECT 1 FROM identity_agents WHERE agent_id=?",
                    (request.actor_id,),
                ).fetchone()
                is None
            ):
                raise _error("foreign_key_violation", "Publication actor is not registered", 409)
            now_us = self._clock()
            manifest_id = f"manifest-input-{uuid4()}"
            cursor.execute(
                "INSERT INTO board_task_publications (operation_id,publication_contract_version,"
                "task_id,canonical_request_json,request_sha256,caller_kid,actor_id,"
                "lock_operation_id,state,admitted_at_us,escrow_id,lock_request_sha256,"
                "lock_receipt_event_id,published_at_us,creation_event_id,result_json,error_json,"
                "request_protocol_version,expected_input_count,base_request_sha256,"
                "freeze_operation_id,canonical_freeze_request_json,freeze_request_sha256,"
                "input_manifest_sha256,publication_contract_sha256,inputs_frozen_at_us,"
                "publish_operation_id,canonical_publish_request_json,publish_request_sha256,"
                "publish_authorized_at_us) VALUES (?,?,?,?,?,?,?,?,'awaiting_inputs',?,"
                "NULL,NULL,NULL,NULL,NULL,NULL,NULL,2,?,?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,"
                "NULL,NULL)",
                (
                    request.publication_operation_id,
                    1,
                    request.task_id,
                    request.canonical_request_json,
                    request.request_sha256,
                    verified_caller_kid,
                    request.actor_id,
                    request.lock_operation_id,
                    now_us,
                    request.expected_input_count,
                    request.base_request_sha256,
                ),
            )
            cursor.execute(
                "INSERT INTO board_artifact_manifests (manifest_id,task_id,kind,actor_id,"
                "format_version,state,canonical_manifest_json,manifest_sha256,frozen_at_us,"
                "publication_operation_id,public_task_id) "
                "VALUES (?,?,'input',?,1,'draft',NULL,NULL,NULL,?,NULL)",
                (
                    manifest_id,
                    request.task_id,
                    request.actor_id,
                    request.publication_operation_id,
                ),
            )
            row = self._required_publication(cursor, request.publication_operation_id)
            assert_publication_integrity(cursor, row)
            result = self._envelope(cursor, row)
            self._db.commit()
            return result
        except ServiceError:
            self._db.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise _error(
                "publication_persistence_failed",
                "Prepublication admission could not be persisted atomically",
                500,
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def freeze_inputs(
        self,
        request: InputManifestFreezeV2,
        *,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        self._authorize_caller(verified_caller_kid)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            replay = self._command_replay(
                cursor,
                "freeze_operation_id",
                request.freeze_operation_id,
            )
            if replay is not None:
                row = replay
                self._assert_stored_caller(row, verified_caller_kid)
                assert_publication_integrity(cursor, row)
                if str(row[22]) != request.freeze_request_sha256:
                    raise _error("operation_conflict", "Freeze identity has different intent", 409)
                result = self._envelope(cursor, row)
                self._db.rollback()
                return result
            self._assert_identity_available(cursor, request.freeze_operation_id)
            row = self._required_publication(cursor, request.publication_operation_id)
            self._assert_stored_caller(row, verified_caller_kid)
            assert_publication_integrity(cursor, row)
            self._validate_owner(row, request.actor_id)
            self._validate_publication_link(row, request.task_id)
            if str(row[8]) != "awaiting_inputs":
                raise _error("invalid_status", "Publication is not awaiting input freeze", 409)
            result = self._freeze_inputs(cursor, row, request)
            self._db.commit()
            return result
        except ServiceError:
            self._db.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise _error(
                "publication_persistence_failed",
                "Input freeze could not be persisted atomically",
                500,
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def _freeze_inputs(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        request: InputManifestFreezeV2,
    ) -> dict[str, Any]:
        manifest = cursor.execute(
            "SELECT task_id,actor_id,state FROM board_artifact_manifests "
            "WHERE manifest_id=? AND publication_operation_id=? AND kind='input'",
            (request.input_manifest_id, request.publication_operation_id),
        ).fetchone()
        if (
            manifest is None
            or str(manifest[0]) != request.task_id
            or str(manifest[1]) != request.actor_id
        ):
            raise _error("manifest_mismatch", "Input manifest does not match publication", 409)
        if str(manifest[2]) != "draft":
            raise _error("manifest_mismatch", "Input manifest is not draft", 409)
        active = cursor.execute(
            "SELECT 1 FROM board_artifact_uploads WHERE manifest_id=? AND state='reserved' LIMIT 1",
            (request.input_manifest_id,),
        ).fetchone()
        if active is not None:
            raise _error("upload_pending", "Input manifest has an active upload", 409)
        ready_rows = cursor.execute(
            f"SELECT {UPLOAD_COLUMNS} FROM board_artifact_uploads "  # nosec B608
            "WHERE manifest_id=? AND state='ready' ORDER BY artifact_id",
            (request.input_manifest_id,),
        ).fetchall()
        if len(ready_rows) != int(row[18]):
            raise _error("input_count_mismatch", "Input manifest count does not match", 409)
        uploads = [tuple(ready_row) for ready_row in ready_rows]
        try:
            for upload in uploads:
                validate_upload_row(upload, expected_caller_kid=str(row[5]))
        except ServiceError as exc:
            raise _error(
                "publication_state_corrupt",
                "Ready input artifact failed integrity validation",
                500,
            ) from exc
        now_us = self._clock()
        canonical_manifest_json, manifest_sha256 = canonical_manifest(
            manifest_id=request.input_manifest_id,
            task_id=request.task_id,
            kind="input",
            actor_id=request.actor_id,
            uploads=uploads,
        )
        publication_contract_sha256 = _sha256(
            {
                "artifact_contract_version": 1,
                "base_request_sha256": str(row[19]),
                "input_manifest_sha256": manifest_sha256,
            }
        )
        cursor.execute(
            "UPDATE board_artifact_manifests SET state='frozen',canonical_manifest_json=?,"
            "manifest_sha256=?,frozen_at_us=? WHERE manifest_id=? AND state='draft'",
            (canonical_manifest_json, manifest_sha256, now_us, request.input_manifest_id),
        )
        if cursor.rowcount != 1:
            raise _error("manifest_mismatch", "Input manifest changed during freeze", 409)
        cursor.execute(
            "UPDATE board_task_publications SET state='awaiting_publish',freeze_operation_id=?,"
            "canonical_freeze_request_json=?,freeze_request_sha256=?,"
            "input_manifest_sha256=?,publication_contract_sha256=?,inputs_frozen_at_us=? "
            "WHERE operation_id=? AND state='awaiting_inputs'",
            (
                request.freeze_operation_id,
                request.canonical_freeze_request_json,
                request.freeze_request_sha256,
                manifest_sha256,
                publication_contract_sha256,
                now_us,
                request.publication_operation_id,
            ),
        )
        if cursor.rowcount != 1:
            raise _error(
                "publication_persistence_failed",
                "Input freeze journal update was not persisted",
                500,
            )
        refreshed = self._required_publication(cursor, request.publication_operation_id)
        assert_publication_integrity(cursor, refreshed)
        return self._envelope(
            cursor,
            refreshed,
        )

    def authorize_publish(
        self,
        request: PublicationPublishV2,
        *,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        self._authorize_caller(verified_caller_kid)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            replay = self._command_replay(
                cursor,
                "publish_operation_id",
                request.publish_operation_id,
            )
            if replay is not None:
                self._assert_stored_caller(replay, verified_caller_kid)
                assert_publication_integrity(cursor, replay)
                if str(replay[28]) != request.publish_request_sha256:
                    raise _error("operation_conflict", "Publish identity has different intent", 409)
                result = self._envelope(cursor, replay)
                self._db.rollback()
                return result
            self._assert_identity_available(cursor, request.publish_operation_id)
            row = self._required_publication(cursor, request.publication_operation_id)
            self._assert_stored_caller(row, verified_caller_kid)
            assert_publication_integrity(cursor, row)
            self._validate_owner(row, request.actor_id)
            self._validate_publication_link(row, request.task_id)
            if str(row[7]) != request.lock_operation_id:
                raise _error(
                    "publication_mismatch",
                    "Lock identity does not match publication",
                    409,
                )
            if str(row[8]) != "awaiting_publish":
                raise _error("invalid_status", "Publication is not awaiting authorization", 409)
            if str(row[24]) != request.publication_contract_sha256:
                raise _error(
                    "publication_contract_mismatch",
                    "Frozen publication contract does not match",
                    409,
                )
            if row[20] is None or row[21] is None or row[22] is None:
                raise _error(
                    "publication_state_corrupt",
                    "Frozen publication identity is missing",
                    500,
                )
            if request.publish_operation_id == str(row[20]):
                raise _error("operation_conflict", "Publish identity conflicts with freeze", 409)
            now_us = self._clock()
            deadline_chain(now_us, self._stored_admission(row))
            cursor.execute(
                "UPDATE board_task_publications SET state='awaiting_lock',publish_operation_id=?,"
                "canonical_publish_request_json=?,publish_request_sha256=?,"
                "publish_authorized_at_us=? WHERE operation_id=? AND state='awaiting_publish'",
                (
                    request.publish_operation_id,
                    request.canonical_publish_request_json,
                    request.publish_request_sha256,
                    now_us,
                    request.publication_operation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise _error(
                    "publication_persistence_failed",
                    "Publish authorization journal update was not persisted",
                    500,
                )
            refreshed = self._required_publication(cursor, request.publication_operation_id)
            assert_publication_integrity(cursor, refreshed)
            result = self._envelope(
                cursor,
                refreshed,
            )
            self._db.commit()
            return result
        except ServiceError:
            self._db.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self._db.rollback()
            raise _error(
                "publication_persistence_failed",
                "Publish authorization could not be persisted atomically",
                500,
            ) from exc
        except Exception:
            self._db.rollback()
            raise

    def get(
        self,
        operation_id: str,
        *,
        verified_caller_kid: str,
        expected_actor_id: str | None,
    ) -> dict[str, Any]:
        self._authorize_caller(verified_caller_kid)
        parsed = parse_operation_id(operation_id)
        cursor = self._db.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = self._publication(cursor, parsed)
            if (
                row is None
                or expected_actor_id is None
                or str(row[6]) != expected_actor_id
                or str(row[5]) != verified_caller_kid
            ):
                raise _error("publication_not_found", "Publication not found", 404)
            assert_publication_integrity(cursor, row)
            result = self._envelope(cursor, row)
            self._db.rollback()
            return result
        except Exception:
            self._db.rollback()
            raise

    def reconcile(
        self,
        operation_id: str,
        *,
        verified_caller_kid: str,
    ) -> dict[str, Any]:
        self._authorize_caller(verified_caller_kid)
        parsed = parse_operation_id(operation_id)
        cursor = self._db.cursor()
        blocked: dict[str, Any] | None = None
        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = self._required_publication(cursor, parsed)
            self._assert_stored_caller(row, verified_caller_kid)
            assert_publication_integrity(cursor, row)
            state = str(row[8])
            if state in {"awaiting_inputs", "awaiting_publish", "published"}:
                result = self._envelope(cursor, row)
                self._db.rollback()
                return result
            if state == "blocked_integrity":
                blocked = self._envelope(cursor, row)
                self._db.rollback()
            else:
                request = self._stored_admission(row)
                try:
                    proof = inspect_v2_lock(cursor, request)
                    if proof is None:
                        result = self._envelope(cursor, row)
                        self._db.rollback()
                        return result
                    manifest_id = str(self._envelope(cursor, row)["input_manifest_id"])
                    result = self._materialize_v2(cursor, row, request, proof, manifest_id, parsed)
                    self._db.commit()
                    return result
                except PublicationEvidenceError as exc:
                    blocked = self._block_v2(cursor, row, exc.reason)
                    self._db.commit()
        except ServiceError:
            self._db.rollback()
            raise
        except Exception as exc:
            self._db.rollback()
            raise _error(
                "publication_persistence_failed",
                "Publication reconciliation could not be persisted atomically",
                500,
            ) from exc
        assert blocked is not None
        raise ServiceError(
            "publication_integrity_blocked",
            "Publication evidence requires reconciliation",
            409,
            {"publication": blocked},
        )

    def _materialize_v2(
        self,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        request: Any,
        proof: Any,
        manifest_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        try:
            materialize_publication(
                cursor, row, request, proof, clock_us=self._clock_us, manifest_id=manifest_id
            )
        except sqlite3.IntegrityError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if code not in {
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                sqlite3.SQLITE_CONSTRAINT_UNIQUE,
                sqlite3.SQLITE_CONSTRAINT_FOREIGNKEY,
                sqlite3.SQLITE_CONSTRAINT_CHECK,
            }:
                raise
            cursor.execute("ROLLBACK TO SAVEPOINT v2_publication")
            cursor.execute("RELEASE SAVEPOINT v2_publication")
            raise PublicationEvidenceError("publication_conflict") from exc
        refreshed = self._required_publication(cursor, operation_id)
        try:
            assert_publication_integrity(cursor, refreshed)
        except ServiceError as exc:
            raise _error(
                "publication_persistence_failed",
                "Published state failed postwrite validation",
                500,
            ) from exc
        return self._envelope(cursor, refreshed)

    def recovery(
        self,
        after: str,
        limit: int,
        *,
        verified_caller_kid: str,
    ) -> dict[str, object]:
        self._authorize_caller(verified_caller_kid)
        if not isinstance(after, str) or type(limit) is not int or not 1 <= limit <= 1000:
            raise _error("invalid_payload", "Recovery cursor or limit is invalid", 400)
        rows = self._db.execute(
            "SELECT operation_id FROM board_task_publications "
            "WHERE caller_kid=? AND request_protocol_version=2 AND state='awaiting_lock' "
            "AND operation_id>? COLLATE BINARY ORDER BY operation_id COLLATE BINARY LIMIT ?",
            (verified_caller_kid, after, limit + 1),
        ).fetchall()
        integrity_cursor = self._db.cursor()
        for row in rows:
            publication = self._required_publication(integrity_cursor, str(row[0]))
            self._assert_stored_caller(publication, verified_caller_kid)
            assert_publication_integrity(integrity_cursor, publication)
        items = [
            {"cursor": str(row[0]), "kind": "native", "identity": str(row[0])}
            for row in rows[:limit]
        ]
        next_after = items[-1]["cursor"] if len(rows) > limit and items else None
        return {"items": items, "next_after": next_after}

    def _authorize_caller(self, value: object) -> None:
        if not isinstance(value, str) or value != self._current_task_board_caller_kid:
            raise _error(
                "operation_caller_mismatch",
                "Only the configured Task Board caller may mutate publications",
                403,
            )

    @staticmethod
    def _assert_stored_caller(
        row: tuple[Any, ...],
        verified_caller_kid: str,
    ) -> None:
        if str(row[5]) != verified_caller_kid:
            raise _error(
                "operation_caller_mismatch",
                "Publication belongs to another verified Task Board caller",
                403,
            )

    def _clock(self) -> int:
        value = self._clock_us()
        if type(value) is not int or value < 0:
            raise _error("invalid_clock", "Prepublication clock is invalid", 500)
        try:
            epoch_us_to_utc_text(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise _error("invalid_clock", "Prepublication clock is invalid", 500) from exc
        return value

    @staticmethod
    def _publication(cursor: sqlite3.Cursor, operation_id: str) -> tuple[Any, ...] | None:
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

    @classmethod
    def _required_publication(cls, cursor: sqlite3.Cursor, operation_id: str) -> tuple[Any, ...]:
        row = cls._publication(cursor, operation_id)
        if row is None:
            raise _error("publication_not_found", "Publication not found", 404)
        return row

    @classmethod
    def _command_replay(
        cls,
        cursor: sqlite3.Cursor,
        column: str,
        operation_id: str,
    ) -> tuple[Any, ...] | None:
        if column not in {"freeze_operation_id", "publish_operation_id"}:
            raise ValueError("unsupported command identity column")
        row = cursor.execute(
            f"SELECT operation_id FROM board_task_publications WHERE {column}=?",  # nosec B608
            (operation_id,),
        ).fetchone()
        return None if row is None else cls._publication(cursor, str(row[0]))

    @staticmethod
    def _validate_owner(row: tuple[Any, ...], actor_id: str) -> None:
        if str(row[6]) != actor_id:
            raise _error("forbidden", "Only the publication actor may perform this command", 403)

    @staticmethod
    def _validate_publication_link(row: tuple[Any, ...], task_id: str) -> None:
        if str(row[2]) != task_id:
            raise _error("publication_mismatch", "Task does not match publication", 409)

    @staticmethod
    def _assert_identity_available(cursor: sqlite3.Cursor, *operation_ids: str) -> None:
        for operation_id in operation_ids:
            task_operation = cursor.execute(
                "SELECT 1 FROM board_task_operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            publication = cursor.execute(
                "SELECT 1 FROM board_task_publications WHERE operation_id=? OR lock_operation_id=? "
                "OR freeze_operation_id=? OR publish_operation_id=? LIMIT 1",
                (operation_id, operation_id, operation_id, operation_id),
            ).fetchone()
            if task_operation is not None or publication is not None:
                raise _error("operation_conflict", "Operation identity is already in use", 409)

    @staticmethod
    def _assert_task_available(cursor: sqlite3.Cursor, task_id: str) -> None:
        for table in (
            "board_task_publications",
            "board_tasks",
            "board_legacy_posting_observations",
            "bank_escrow",
        ):
            if (
                cursor.execute(
                    f"SELECT 1 FROM {table} WHERE task_id=? LIMIT 1",  # nosec B608
                    (task_id,),
                ).fetchone()
                is not None
            ):
                raise _error("task_id_conflict", "Task identity already has history", 409)

    @staticmethod
    def _replay_admission(
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        request: PublicationAdmissionV2,
    ) -> dict[str, Any]:
        if int(row[17]) != 2 or str(row[4]) != request.request_sha256:
            raise _error("operation_conflict", "Admission identity has different intent", 409)
        return PrepublicationWriter._envelope(cursor, row)

    @staticmethod
    def _stored_admission(row: tuple[Any, ...]) -> Any:
        try:
            decoded: object = json.loads(str(row[3]))
            return parse_publication_admission_v2(decoded)
        except (ServiceError, TypeError, ValueError) as exc:
            raise _error("publication_state_corrupt", "Stored admission is invalid", 500) from exc

    @classmethod
    def _block_v2(
        cls,
        cursor: sqlite3.Cursor,
        row: tuple[Any, ...],
        reason: str,
    ) -> dict[str, Any]:
        error = {
            "error": "publication_integrity_blocked",
            "message": "Publication evidence requires reconciliation",
            "details": {"reason": reason},
        }
        cursor.execute(
            "UPDATE board_task_publications SET state='blocked_integrity',escrow_id=NULL,"
            "lock_request_sha256=NULL,lock_receipt_event_id=NULL,published_at_us=NULL,"
            "creation_event_id=NULL,result_json=NULL,error_json=? "
            "WHERE operation_id=? AND state='awaiting_lock'",
            (json.dumps(error, sort_keys=True, separators=(",", ":")), str(row[0])),
        )
        if cursor.rowcount != 1:
            raise _error(
                "publication_persistence_failed", "Publication block was not persisted", 500
            )
        refreshed = cls._required_publication(cursor, str(row[0]))
        try:
            assert_publication_integrity(cursor, refreshed)
        except ServiceError as exc:
            raise _error(
                "publication_persistence_failed",
                "Blocked state failed postwrite validation",
                500,
            ) from exc
        return cls._envelope(cursor, refreshed)

    @staticmethod
    def _envelope(cursor: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
        manifest = cursor.execute(
            "SELECT manifest_id FROM board_artifact_manifests "
            "WHERE publication_operation_id=? AND kind='input'",
            (str(row[0]),),
        ).fetchone()
        if manifest is None:
            raise _error("publication_state_corrupt", "Publication input manifest is missing", 500)
        result = None if row[15] is None else json.loads(str(row[15]))
        error = None if row[16] is None else json.loads(str(row[16]))
        return {
            "protocol_version": int(row[17]),
            "publication_operation_id": str(row[0]),
            "task_id": str(row[2]),
            "actor_id": str(row[6]),
            "publication_state": str(row[8]),
            "request_sha256": str(row[4]),
            "base_request_sha256": None if row[19] is None else str(row[19]),
            "expected_input_count": None if row[18] is None else int(row[18]),
            "input_manifest_id": str(manifest[0]),
            "input_manifest_sha256": None if row[23] is None else str(row[23]),
            "publication_contract_sha256": None if row[24] is None else str(row[24]),
            "inputs_frozen_at_us": None if row[25] is None else int(row[25]),
            "freeze_operation_id": None if row[20] is None else str(row[20]),
            "publish_operation_id": None if row[26] is None else str(row[26]),
            "publish_authorized_at_us": None if row[29] is None else int(row[29]),
            "lock_operation_id": str(row[7]),
            "escrow_id": None if row[10] is None else str(row[10]),
            "lock_request_sha256": None if row[11] is None else str(row[11]),
            "lock_receipt_event_id": None if row[12] is None else int(row[12]),
            "admitted_at": fixed_utc(int(row[9])),
            "published_at": None if row[13] is None else fixed_utc(int(row[13])),
            "creation_event_id": None if row[14] is None else int(row[14]),
            "result": result,
            "error": error,
        }


__all__ = ["PrepublicationWriter"]

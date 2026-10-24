"""Real L1-produced legacy continuation fixture for MP-09 L2 tests."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from db_gateway_service.artifact_upload_requests import (
    ArtifactUploadRequest,
    parse_artifact_policy,
    parse_artifact_upload_request,
)
from db_gateway_service.services.artifact_writer import ArtifactWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from tests.fakes.mp09_legacy_continuation_fixture import (
    BOARD_KID,
    TASK_ID,
    WORKER_ID,
    LegacyContinuationFixture,
    MutableClock,
    claim_acceptance,
    full_snapshot,
    open_preserved_v1_cutover_fixture,
    prepare_accept_request,
    prepare_legacy_acceptance,
    submit_real_bid,
)

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

SUBMISSION_MANIFEST_ID: Final[str] = "manifest-mp09-legacy-submission"
BODY_A: Final[bytes] = b"legacy continuation alpha\n"
BODY_B: Final[bytes] = b"legacy continuation beta\n"

type SubmissionWriteTarget = Literal[
    "validation_select",
    "manifest_update",
    "task_update",
    "timing_update",
    "event_insert",
    "operation_insert",
]
type SubmissionFailMode = Literal["operational_before", "runtime_before", "zero_row_after"]

_MARKERS: Final[dict[SubmissionWriteTarget, tuple[str, str]]] = {
    "validation_select": ("SELECT artifact_contract_version", "FROM board_task_artifact_contracts"),
    "manifest_update": ("UPDATE board_artifact_manifests", "state='frozen'"),
    "task_update": ("UPDATE board_tasks", "status='submitted'"),
    "timing_update": ("UPDATE board_task_contracts", "review_cutoff_us"),
    "event_insert": ("INSERT INTO events", "VALUES"),
    "operation_insert": ("INSERT INTO board_task_operations", "VALUES"),
}


@dataclass(slots=True)
class LegacySubmissionFixture:
    legacy: LegacyContinuationFixture
    acceptance: dict[str, Any]
    binding_kind: str
    binding_sha256: str
    artifacts: ArtifactWriter

    @property
    def connection(self) -> sqlite3.Connection:
        return self.legacy.connection

    @property
    def clock(self) -> MutableClock:
        return self.legacy.clock

    @property
    def operations(self) -> TaskOperationWriter:
        return self.legacy.operation_writer

    def close(self) -> None:
        self.legacy.close()


@dataclass(slots=True)
class EvidenceReadGuard(AbstractContextManager["EvidenceReadGuard"]):
    connection: sqlite3.Connection
    reads: list[str]

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.reads = []

    def __enter__(self) -> EvidenceReadGuard:
        self.connection.set_authorizer(self._authorize)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        self.connection.set_authorizer(None)
        return False

    def _authorize(
        self,
        action: int,
        table: str | None,
        _column: str | None,
        _database: str | None,
        _source: str | None,
    ) -> int:
        if action != sqlite3.SQLITE_READ or not isinstance(table, str):
            return sqlite3.SQLITE_OK
        evidence = {
            "board_tasks",
            "board_task_contracts",
            "board_bids",
            "board_task_artifact_contracts",
            "board_legacy_artifact_observations",
            "board_task_publications",
            "bank_escrow",
        }
        if table in evidence:
            self.reads.append(table)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK


class _FailCursor:
    def __init__(
        self,
        cursor: sqlite3.Cursor,
        *,
        target: SubmissionWriteTarget,
        mode: SubmissionFailMode,
    ) -> None:
        self._cursor = cursor
        self._target = target
        self._mode = mode
        self._fired = False
        self._zero = False

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] | dict[str, object] = (),
    ) -> _FailCursor:
        normalized = " ".join(sql.split())
        prefix, marker = _MARKERS[self._target]
        selected = not self._fired and normalized.startswith(prefix) and marker in normalized
        if selected and self._mode == "operational_before":
            self._fired = True
            raise sqlite3.OperationalError(f"injected {self._target}")
        if selected and self._mode == "runtime_before":
            self._fired = True
            raise RuntimeError(f"injected {self._target}")
        self._cursor.execute(sql, parameters)
        self._zero = bool(selected and self._mode == "zero_row_after")
        if selected:
            self._fired = True
        return self

    @property
    def rowcount(self) -> int:
        return 0 if self._zero else self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self._cursor.lastrowid

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _FailConnection:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        target: SubmissionWriteTarget,
        mode: SubmissionFailMode,
    ) -> None:
        self._connection = connection
        self._target = target
        self._mode = mode

    def cursor(self) -> _FailCursor:
        return _FailCursor(self._connection.cursor(), target=self._target, mode=self._mode)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def prepare_legacy_submission(
    database_path: Path,
    *,
    provenance: Literal["none", "preserved_v1"],
) -> LegacySubmissionFixture:
    if provenance == "preserved_v1":
        legacy = open_preserved_v1_cutover_fixture(database_path)
        submit_real_bid(legacy)
        prepare_accept_request(legacy)
    else:
        legacy = prepare_legacy_acceptance(database_path)
    try:
        acceptance = claim_acceptance(legacy)
        result = acceptance["result"]
        assert isinstance(result, dict)
        kind = result["accepted_binding_kind"]
        digest = result["accepted_binding_sha256"]
        assert kind == "legacy_accepted_continuation"
        assert isinstance(digest, str) and len(digest) == 64
        row = legacy.connection.execute(
            "SELECT observation_status,observation_sha256 FROM "
            "board_legacy_artifact_observations WHERE task_id=?",
            (TASK_ID,),
        ).fetchone()
        assert row is not None and tuple(row) == ("verified", digest)
        legacy.connection.execute(
            "INSERT INTO board_artifact_manifests (manifest_id,task_id,kind,actor_id,"
            "format_version,state,canonical_manifest_json,manifest_sha256,frozen_at_us,"
            "publication_operation_id,public_task_id) VALUES (?,?,'submission',?,1,'draft',"
            "NULL,NULL,NULL,NULL,?)",
            (SUBMISSION_MANIFEST_ID, TASK_ID, WORKER_ID, TASK_ID),
        )
        legacy.connection.commit()
        policy = parse_artifact_policy(
            {
                "contract_version": 1,
                "max_file_bytes": 64,
                "max_files_per_manifest": 3,
                "max_manifest_bytes": 128,
                "reservation_ttl_seconds": 300,
                "reservation_renew_before_expiry_seconds": 60,
                "max_upload_duration_seconds": 900,
            }
        )
        artifacts = ArtifactWriter(
            legacy.connection,
            clock_us=legacy.clock,
            policy=policy,
            current_task_board_caller_kid=BOARD_KID,
        )
        assert legacy.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        return LegacySubmissionFixture(legacy, acceptance, str(kind), digest, artifacts)
    except BaseException:
        legacy.close()
        raise


def upload_request(
    fixture: LegacySubmissionFixture,
    number: int,
    *,
    body: bytes,
    binding_kind: str | None = None,
    binding_sha256: str | None = None,
) -> ArtifactUploadRequest:
    return parse_artifact_upload_request(
        {
            "protocol_version": 1,
            "upload_operation_id": f"76000000-0000-4000-8000-{number:012d}",
            "resource": "POST /board/artifact-uploads/reserve",
            "task_id": TASK_ID,
            "manifest_id": SUBMISSION_MANIFEST_ID,
            "actor_id": WORKER_ID,
            "actor_role": "worker",
            "display_filename": f"artifact-{number}.txt",
            "evidence_format": "text",
            "declared_size_bytes": len(body),
            "content_sha256": hashlib.sha256(body).hexdigest(),
            "expected_lifecycle_version": 1,
            "accepted_binding_kind": binding_kind or fixture.binding_kind,
            "accepted_binding_sha256": binding_sha256 or fixture.binding_sha256,
        }
    )


def reserve_upload(
    fixture: LegacySubmissionFixture,
    request: ArtifactUploadRequest,
) -> dict[str, Any]:
    return fixture.artifacts.reserve_upload(request, verified_caller_kid=BOARD_KID)


def finalize_upload(
    fixture: LegacySubmissionFixture,
    request: ArtifactUploadRequest,
    reserved: dict[str, Any],
) -> dict[str, Any]:
    attempt = reserved["attempt"]
    return fixture.artifacts.finalize_upload(
        request.upload_operation_id,
        owner_id=attempt["owner_id"],
        generation=attempt["generation"],
        attempt_nonce=attempt["attempt_nonce"],
        observed_size_bytes=request.declared_size_bytes,
        observed_sha256=request.content_sha256,
        verified_caller_kid=BOARD_KID,
    )


def ready_upload(
    fixture: LegacySubmissionFixture,
    number: int,
    *,
    body: bytes,
) -> tuple[ArtifactUploadRequest, dict[str, Any]]:
    request = upload_request(fixture, number, body=body)
    reserved = reserve_upload(fixture, request)
    return request, finalize_upload(fixture, request, reserved)


def submission_request(
    fixture: LegacySubmissionFixture,
    number: int,
    *,
    binding_kind: str | None = None,
    binding_sha256: str | None = None,
) -> TaskOperationRequest:
    return parse_task_operation_request(
        {
            "protocol_version": 2,
            "operation_id": f"op-77000000-0000-4000-8000-{number:012d}",
            "task_id": TASK_ID,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": WORKER_ID,
            "arguments": {
                "worker_id": WORKER_ID,
                "manifest_id": SUBMISSION_MANIFEST_ID,
                "accepted_binding_kind": binding_kind or fixture.binding_kind,
                "accepted_binding_sha256": binding_sha256 or fixture.binding_sha256,
            },
        }
    )


def failpoint_submission_writer(
    fixture: LegacySubmissionFixture,
    *,
    target: SubmissionWriteTarget,
    mode: SubmissionFailMode,
) -> TaskOperationWriter:
    return TaskOperationWriter(
        _FailConnection(fixture.connection, target=target, mode=mode),
        clock_us=fixture.clock,
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=BOARD_KID,
    )


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


__all__ = [
    "BODY_A",
    "BODY_B",
    "SUBMISSION_MANIFEST_ID",
    "EvidenceReadGuard",
    "LegacySubmissionFixture",
    "SubmissionFailMode",
    "SubmissionWriteTarget",
    "canonical_json",
    "failpoint_submission_writer",
    "finalize_upload",
    "full_snapshot",
    "prepare_legacy_submission",
    "ready_upload",
    "reserve_upload",
    "submission_request",
    "upload_request",
]

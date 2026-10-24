"""T-126 red proof for atomic protocol-v2 submission manifests."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services.artifact_writer import ArtifactWriter
from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from db_gateway_service.task_operation_requests import (
    TaskOperationRequest,
    parse_task_operation_request,
)
from db_gateway_service.task_time import epoch_us_to_utc_text
from tests.fakes.mp09_v6_artifact_fixture import (
    BOARD_KID,
    LEGACY_BINDING,
    NATIVE_BINDING,
    NOW_US,
    TASK_ID,
    MutableClock,
    attempt_fence,
    explicit_policy,
    open_v6_database,
    require_v6_api,
    seed_accepted_task,
    upload_payload,
)

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

pytestmark = pytest.mark.unit

_TASK_ID = TASK_ID
_MANIFEST_ID = "manifest-v6-reservation"
_WORKER_ID = "worker-1"
_SUBMITTED_AT_US = NOW_US + 30_000_000


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _open_submission_database(
    tmp_path: Path,
) -> tuple[Any, sqlite3.Connection, MutableClock, ArtifactWriter, TaskOperationWriter]:
    api = require_v6_api()
    connection = open_v6_database(tmp_path / "submission-manifest.sqlite3")
    seed_accepted_task(
        connection,
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
    )
    clock = MutableClock()
    artifacts = ArtifactWriter(
        connection,
        clock_us=clock,
        policy=explicit_policy(api),
        current_task_board_caller_kid=BOARD_KID,
    )
    operations = TaskOperationWriter(
        connection,
        clock_us=clock,
        deadline_actor_id="system-task-board-deadline",
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=BOARD_KID,
    )
    return api, connection, clock, artifacts, operations


def _upload_operation(number: int) -> str:
    return f"30000000-0000-4000-8000-{number:012d}"


def _task_operation(number: int) -> str:
    return f"op-40000000-0000-4000-8000-{number:012d}"


def _ready_artifact(
    api: Any,
    artifacts: ArtifactWriter,
    clock: MutableClock,
    *,
    number: int,
    display_filename: str,
    body: bytes,
) -> dict[str, object]:
    clock.now_us = NOW_US + number * 1_000_000
    payload = upload_payload(
        operation_id=_upload_operation(number),
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
        body=body,
        content_sha256=_sha256(body),
        display_filename=display_filename,
    )
    request = api.requests.parse_artifact_upload_request(payload)
    reserved = artifacts.reserve_upload(request, verified_caller_kid=BOARD_KID)
    finalized = artifacts.finalize_upload(
        request.upload_operation_id,
        **attempt_fence(reserved),
        observed_size_bytes=len(body),
        observed_sha256=_sha256(body),
        verified_caller_kid=BOARD_KID,
    )
    assert finalized["state"] == "ready"
    return finalized


def _submission_payload(
    *,
    number: int = 1,
    binding_sha256: str = NATIVE_BINDING,
) -> dict[str, object]:
    return {
        "protocol_version": 2,
        "operation_id": _task_operation(number),
        "task_id": _TASK_ID,
        "command": "submit_deliverable",
        "expected_status": "accepted",
        "expected_lifecycle_version": 1,
        "actor_kind": "agent",
        "actor_id": _WORKER_ID,
        "arguments": {
            "worker_id": _WORKER_ID,
            "manifest_id": _MANIFEST_ID,
            "accepted_binding_kind": "publication_contract",
            "accepted_binding_sha256": binding_sha256,
        },
    }


def _parse_submission_v2(payload: dict[str, object]) -> TaskOperationRequest:
    try:
        request = parse_task_operation_request(payload)
    except ServiceError as exc:
        pytest.fail(
            "Missing protocol-v2 submit_deliverable request capability: "
            f"{exc.error} ({exc.status_code})"
        )
    assert request.command == "submit_deliverable"
    supplied_arguments = payload["arguments"]
    assert isinstance(supplied_arguments, dict)
    for field in (
        "worker_id",
        "manifest_id",
        "accepted_binding_kind",
        "accepted_binding_sha256",
    ):
        assert request.argument(field) == supplied_arguments[field]
    return request


def _snapshot(connection: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    tables = (
        "events",
        "board_tasks",
        "board_task_contracts",
        "board_task_artifact_contracts",
        "board_legacy_artifact_observations",
        "board_artifact_cutover",
        "board_artifact_manifests",
        "board_artifact_uploads",
        "board_artifact_upload_attempts",
        "board_assets",
        "board_task_operations",
    )
    return {
        table: [
            tuple(row)
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")  # nosec B608
        ]
        for table in tables
    }


def _record_pre_cutover_asset(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO board_assets (asset_id,task_id,uploader_id,filename,content_type,"
        "size_bytes,storage_path,content_hash,uploaded_at) VALUES "
        "('asset-v1-history',?,?, 'legacy.txt','text/plain',7,"
        "'live/legacy/history.blob',?,'2099-01-02T00:00:01Z')",
        (_TASK_ID, _WORKER_ID, _sha256(b"legacy\n")),
    )
    connection.commit()


def _protocol_v1_request(number: int) -> TaskOperationRequest:
    return parse_task_operation_request(
        {
            "protocol_version": 1,
            "operation_id": _task_operation(number),
            "task_id": _TASK_ID,
            "command": "submit_deliverable",
            "expected_status": "accepted",
            "expected_lifecycle_version": 1,
            "actor_kind": "agent",
            "actor_id": _WORKER_ID,
            "arguments": {"worker_id": _WORKER_ID},
        }
    )


def _delete_artifact_governance(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM board_task_artifact_contracts WHERE task_id=?", (_TASK_ID,))
    connection.execute(
        "DELETE FROM board_legacy_artifact_observations WHERE task_id=?", (_TASK_ID,)
    )
    connection.execute("DELETE FROM board_artifact_manifests WHERE task_id=?", (_TASK_ID,))
    connection.commit()


def _insert_exact_row(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    values: tuple[object, ...],
) -> None:
    column_sql = ",".join(columns)
    placeholders = ",".join("?" for _ in values)
    connection.execute(
        f"INSERT INTO {table} ({column_sql}) VALUES ({placeholders})",  # nosec B608
        values,
    )


def _manifest_item(connection: sqlite3.Connection, artifact_id: str) -> dict[str, object]:
    row = connection.execute(
        "SELECT artifact_id,display_filename,evidence_format,media_type,declared_size_bytes,"
        "content_sha256,actor_id,finalized_at_us FROM board_artifact_uploads "
        "WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    assert row is not None
    return {
        "artifact_id": str(row[0]),
        "display_filename": str(row[1]),
        "evidence_format": str(row[2]),
        "media_type": str(row[3]),
        "byte_size": int(row[4]),
        "sha256": str(row[5]),
        "provenance": "v6_upload",
        "actor_id": str(row[6]),
        "uploaded_at": epoch_us_to_utc_text(int(row[7])),
    }


def test_protocol_v2_submission_arguments_are_strict_and_canonical() -> None:
    valid_payload = _submission_payload()
    valid_request = _parse_submission_v2(valid_payload)
    canonical_identity = dict(valid_payload)
    del canonical_identity["operation_id"]
    expected_json = _canonical_json(canonical_identity)
    assert valid_request.canonical_json == expected_json
    assert valid_request.request_sha256 == hashlib.sha256(expected_json.encode()).hexdigest()
    assert dict(valid_request.arguments) == {
        "worker_id": _WORKER_ID,
        "manifest_id": _MANIFEST_ID,
        "accepted_binding_kind": "publication_contract",
        "accepted_binding_sha256": NATIVE_BINDING,
    }

    argument_names = tuple(valid_payload["arguments"])
    for missing in argument_names:
        malformed = _submission_payload(number=2)
        arguments = dict(malformed["arguments"])
        del arguments[missing]
        malformed["arguments"] = arguments
        with pytest.raises(ServiceError) as raised:
            parse_task_operation_request(malformed)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")

    malformed = _submission_payload(number=3)
    arguments = dict(malformed["arguments"])
    arguments["implicit_default"] = "forbidden"
    malformed["arguments"] = arguments
    with pytest.raises(ServiceError) as raised:
        parse_task_operation_request(malformed)
    assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")

    invalid_arguments: tuple[tuple[str, object], ...] = (
        ("worker_id", 1),
        ("manifest_id", 1),
        ("accepted_binding_kind", 1),
        ("accepted_binding_kind", "legacy"),
        ("accepted_binding_sha256", 1),
        ("accepted_binding_sha256", NATIVE_BINDING.upper()),
        ("accepted_binding_sha256", "f" * 63),
    )
    for number, (field, value) in enumerate(invalid_arguments, start=4):
        malformed = _submission_payload(number=number)
        arguments = dict(malformed["arguments"])
        arguments[field] = value
        malformed["arguments"] = arguments
        with pytest.raises(ServiceError) as raised:
            parse_task_operation_request(malformed)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload"), field


def test_protocol_v2_submission_freezes_ready_artifacts_and_one_lifecycle_result(
    tmp_path: Path,
) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        first = _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="zeta.txt",
            body=b"zeta\n",
        )
        second = _ready_artifact(
            api,
            artifacts,
            clock,
            number=2,
            display_filename="alpha.txt",
            body=b"alpha\n",
        )
        request = _parse_submission_v2(_submission_payload())
        clock.now_us = _SUBMITTED_AT_US
        result = operations.claim_operation(request, BOARD_KID)

        items = sorted(
            (
                _manifest_item(connection, str(first["artifact_id"])),
                _manifest_item(connection, str(second["artifact_id"])),
            ),
            key=lambda item: str(item["artifact_id"]),
        )
        canonical = _canonical_json(
            {
                "format_version": 1,
                "manifest_id": _MANIFEST_ID,
                "task_id": _TASK_ID,
                "kind": "submission",
                "actor_id": _WORKER_ID,
                "artifacts": items,
            }
        )
        manifest_sha256 = hashlib.sha256(canonical.encode()).hexdigest()
        manifest = connection.execute(
            "SELECT state,canonical_manifest_json,manifest_sha256,frozen_at_us "
            "FROM board_artifact_manifests WHERE manifest_id=?",
            (_MANIFEST_ID,),
        ).fetchone()
        assert manifest is not None
        assert tuple(manifest) == ("frozen", canonical, manifest_sha256, _SUBMITTED_AT_US)

        task = connection.execute(
            "SELECT status,submitted_at,review_deadline,lifecycle_version,review_cutoff_us "
            "FROM board_tasks JOIN board_task_contracts USING(task_id) "
            "WHERE board_tasks.task_id=?",
            (_TASK_ID,),
        ).fetchone()
        assert task is not None
        assert tuple(task) == (
            "submitted",
            epoch_us_to_utc_text(_SUBMITTED_AT_US),
            epoch_us_to_utc_text(_SUBMITTED_AT_US + 60_000_000),
            2,
            _SUBMITTED_AT_US + 60_000_000,
        )
        assert result["protocol_version"] == 2
        assert result["operation_state"] == "completed"
        assert result["result"] == {
            "task_id": _TASK_ID,
            "command": "submit_deliverable",
            "status": "submitted",
            "lifecycle_version": 2,
            "worker_id": _WORKER_ID,
            "manifest_id": _MANIFEST_ID,
            "manifest_sha256": manifest_sha256,
            "event_id": result["event_id"],
        }
        events = connection.execute(
            "SELECT event_source,event_type,task_id,agent_id FROM events "
            "WHERE event_type='task.submitted'"
        ).fetchall()
        assert [tuple(row) for row in events] == [("board", "task.submitted", _TASK_ID, _WORKER_ID)]
        stored_result = connection.execute(
            "SELECT result_json FROM board_task_operations WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        assert stored_result is not None
        assert json.loads(str(stored_result[0])) == result["result"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_submission_binding_mismatch_leaves_full_database_unchanged(tmp_path: Path) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="answer.txt",
            body=b"answer\n",
        )
        request = _parse_submission_v2(_submission_payload(binding_sha256="f" * 64))
        before = _snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            409,
            "accepted_binding_mismatch",
        )
        assert _snapshot(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize("changed_field", ["manifest_id", "accepted_binding_sha256"])
def test_changed_protocol_v2_replay_uses_existing_operation_id_conflict_without_mutation(
    tmp_path: Path,
    changed_field: str,
) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="answer.txt",
            body=b"answer\n",
        )
        original_request = _parse_submission_v2(_submission_payload())
        clock.now_us = _SUBMITTED_AT_US
        operations.claim_operation(original_request, BOARD_KID)
        changed_payload = _submission_payload()
        changed_arguments = dict(changed_payload["arguments"])
        changed_arguments[changed_field] = (
            "manifest-other" if changed_field == "manifest_id" else "f" * 64
        )
        changed_payload["arguments"] = changed_arguments
        changed_request = _parse_submission_v2(changed_payload)
        assert changed_request.operation_id == original_request.operation_id
        assert changed_request.request_sha256 != original_request.request_sha256
        before = _snapshot(connection)

        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(changed_request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (409, "operation_id_conflict")
        assert _snapshot(connection) == before
    finally:
        connection.close()


def test_submission_without_ready_artifacts_returns_no_assets_without_mutation(
    tmp_path: Path,
) -> None:
    _api, connection, _clock, _artifacts, operations = _open_submission_database(tmp_path)
    try:
        request = _parse_submission_v2(_submission_payload())
        before = _snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (400, "no_assets")
        assert _snapshot(connection) == before
    finally:
        connection.close()


def test_active_upload_blocks_manifest_freeze_and_submission_transition(tmp_path: Path) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="ready.txt",
            body=b"ready\n",
        )
        payload = upload_payload(
            operation_id=_upload_operation(2),
            task_id=_TASK_ID,
            manifest_id=_MANIFEST_ID,
            body=b"pending\n",
            content_sha256=_sha256(b"pending\n"),
            display_filename="pending.txt",
        )
        pending = api.requests.parse_artifact_upload_request(payload)
        artifacts.reserve_upload(pending, verified_caller_kid=BOARD_KID)
        request = _parse_submission_v2(_submission_payload())
        before = _snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (409, "upload_pending")
        assert _snapshot(connection) == before
    finally:
        connection.close()


def test_submission_freezes_ready_artifact_and_excludes_orphaned_upload(tmp_path: Path) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        ready = _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="ready.txt",
            body=b"ready\n",
        )
        clock.now_us = NOW_US + 2_000_000
        payload = upload_payload(
            operation_id=_upload_operation(2),
            task_id=_TASK_ID,
            manifest_id=_MANIFEST_ID,
            body=b"orphaned\n",
            content_sha256=_sha256(b"orphaned\n"),
            display_filename="orphaned.txt",
        )
        orphan_request = api.requests.parse_artifact_upload_request(payload)
        reserved = artifacts.reserve_upload(orphan_request, verified_caller_kid=BOARD_KID)
        orphaned = artifacts.orphan_upload(
            orphan_request.upload_operation_id,
            **attempt_fence(reserved),
            failure_code="client_disconnected",
            verified_caller_kid=BOARD_KID,
        )
        assert orphaned["state"] == "orphaned"

        request = _parse_submission_v2(_submission_payload())
        clock.now_us = _SUBMITTED_AT_US
        operations.claim_operation(request, BOARD_KID)
        manifest = connection.execute(
            "SELECT canonical_manifest_json FROM board_artifact_manifests WHERE manifest_id=?",
            (_MANIFEST_ID,),
        ).fetchone()
        assert manifest is not None
        decoded: object = json.loads(str(manifest[0]))
        assert isinstance(decoded, dict)
        items = decoded.get("artifacts")
        assert isinstance(items, list)
        assert [item["artifact_id"] for item in items if isinstance(item, dict)] == [
            ready["artifact_id"]
        ]
        assert orphaned["artifact_id"] not in {
            item["artifact_id"] for item in items if isinstance(item, dict)
        }
    finally:
        connection.close()


def test_exact_submission_replay_survives_status_drift_with_same_manifest_result(
    tmp_path: Path,
) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="answer.txt",
            body=b"answer\n",
        )
        request = _parse_submission_v2(_submission_payload())
        clock.now_us = _SUBMITTED_AT_US
        original = operations.claim_operation(request, BOARD_KID)
        manifest_before = connection.execute(
            "SELECT canonical_manifest_json,manifest_sha256,frozen_at_us "
            "FROM board_artifact_manifests WHERE manifest_id=?",
            (_MANIFEST_ID,),
        ).fetchone()
        event_count_before = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='task.submitted'"
        ).fetchone()
        connection.execute("UPDATE board_tasks SET status='approved' WHERE task_id=?", (_TASK_ID,))
        connection.execute(
            "UPDATE board_task_contracts SET lifecycle_version=3 WHERE task_id=?", (_TASK_ID,)
        )
        connection.commit()

        replay = operations.claim_operation(request, BOARD_KID)
        assert replay == original
        manifest_after = connection.execute(
            "SELECT canonical_manifest_json,manifest_sha256,frozen_at_us "
            "FROM board_artifact_manifests WHERE manifest_id=?",
            (_MANIFEST_ID,),
        ).fetchone()
        assert manifest_after is not None and manifest_before is not None
        assert tuple(manifest_after) == tuple(manifest_before)
        event_count_after = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type='task.submitted'"
        ).fetchone()
        assert event_count_after is not None and event_count_before is not None
        assert tuple(event_count_after) == tuple(event_count_before) == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize("failure_target", ["manifest", "event"])
def test_submission_persistence_failure_rolls_back_manifest_lifecycle_event_and_result(
    tmp_path: Path,
    failure_target: str,
) -> None:
    api, connection, clock, artifacts, operations = _open_submission_database(tmp_path)
    try:
        _ready_artifact(
            api,
            artifacts,
            clock,
            number=1,
            display_filename="answer.txt",
            body=b"answer\n",
        )
        request = _parse_submission_v2(_submission_payload())
        if failure_target == "manifest":
            connection.execute(
                "CREATE TRIGGER inject_submission_manifest_failure "
                "BEFORE UPDATE OF state ON board_artifact_manifests "
                "WHEN NEW.state='frozen' BEGIN "
                "SELECT RAISE(ABORT,'injected manifest persistence failure'); END"
            )
        else:
            connection.execute(
                "CREATE TRIGGER inject_submission_event_failure "
                "BEFORE INSERT ON events WHEN NEW.event_type='task.submitted' BEGIN "
                "SELECT RAISE(ABORT,'injected event persistence failure'); END"
            )
        connection.commit()
        before = _snapshot(connection)
        clock.now_us = _SUBMITTED_AT_US
        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "task_operation_persistence_failed",
        )
        assert _snapshot(connection) == before
    finally:
        connection.close()


def test_stored_pre_cutover_v1_operation_replays_after_governance_and_state_drift(
    tmp_path: Path,
) -> None:
    """Replay after captured governance rows plus intentional status/version drift.

    The fixture restores exact previously captured contract and observation rows.
    It exercises stored-operation precedence, not migration correctness.
    """
    require_v6_api()
    connection = open_v6_database(tmp_path / "pre-cutover-v1.sqlite3")
    try:
        seed_accepted_task(
            connection,
            task_id=_TASK_ID,
            manifest_id=_MANIFEST_ID,
            binding_kind="legacy_accepted_continuation",
            binding_sha256=LEGACY_BINDING,
        )
        contract_columns = tuple(
            str(row[1])
            for row in connection.execute("PRAGMA table_info(board_task_artifact_contracts)")
        )
        contract_row = connection.execute(
            "SELECT * FROM board_task_artifact_contracts WHERE task_id=?", (_TASK_ID,)
        ).fetchone()
        observation_columns = tuple(
            str(row[1])
            for row in connection.execute("PRAGMA table_info(board_legacy_artifact_observations)")
        )
        observation_row = connection.execute(
            "SELECT * FROM board_legacy_artifact_observations WHERE task_id=?", (_TASK_ID,)
        ).fetchone()
        assert contract_row is not None and observation_row is not None
        _delete_artifact_governance(connection)
        assert (
            connection.execute("SELECT COUNT(*) FROM board_task_artifact_contracts").fetchone()[0]
            == 0
        )
        assert connection.execute("SELECT COUNT(*) FROM board_artifact_cutover").fetchone()[0] == 0

        _record_pre_cutover_asset(connection)
        clock = MutableClock(now_us=_SUBMITTED_AT_US)
        operations = TaskOperationWriter(
            connection,
            clock_us=clock,
            deadline_actor_id="system-task-board-deadline",
            platform_public_key="ed25519:test-platform-authority",
            current_task_board_caller_kid=BOARD_KID,
        )
        request = _protocol_v1_request(9)
        original = operations.claim_operation(request, BOARD_KID)

        _insert_exact_row(
            connection,
            "board_legacy_artifact_observations",
            observation_columns,
            tuple(observation_row),
        )
        _insert_exact_row(
            connection,
            "board_task_artifact_contracts",
            contract_columns,
            tuple(contract_row),
        )
        connection.execute("UPDATE board_tasks SET status='approved' WHERE task_id=?", (_TASK_ID,))
        connection.execute(
            "UPDATE board_task_contracts SET lifecycle_version=3 WHERE task_id=?", (_TASK_ID,)
        )
        connection.commit()

        replay = operations.claim_operation(request, BOARD_KID)
        assert replay == original
        assert replay["protocol_version"] == 1
        assert replay["result"] == original["result"]
        operation_count = connection.execute(
            "SELECT COUNT(*) FROM board_task_operations WHERE operation_id=?",
            (request.operation_id,),
        ).fetchone()
        assert operation_count is not None
        assert tuple(operation_count) == (1,)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_artifact_manifests WHERE task_id=?", (_TASK_ID,)
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("binding_kind", "binding_sha256"),
    [
        ("publication_contract", NATIVE_BINDING),
        ("legacy_accepted_continuation", LEGACY_BINDING),
    ],
)
def test_governed_task_rejects_new_protocol_v1_submission_without_mutation(
    tmp_path: Path,
    binding_kind: str,
    binding_sha256: str,
) -> None:
    connection = open_v6_database(tmp_path / f"governed-{binding_kind}.sqlite3")
    try:
        seed_accepted_task(
            connection,
            task_id=_TASK_ID,
            manifest_id=_MANIFEST_ID,
            binding_kind=binding_kind,
            binding_sha256=binding_sha256,
        )
        _record_pre_cutover_asset(connection)
        operations = TaskOperationWriter(
            connection,
            clock_us=MutableClock(now_us=_SUBMITTED_AT_US),
            deadline_actor_id="system-task-board-deadline",
            platform_public_key="ed25519:test-platform-authority",
            current_task_board_caller_kid=BOARD_KID,
        )
        request = _protocol_v1_request(10)
        before = _snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")
        assert _snapshot(connection) == before
    finally:
        connection.close()


def test_completed_cutover_without_artifact_contract_cannot_fall_back_to_protocol_v1(
    tmp_path: Path,
) -> None:
    connection = open_v6_database(tmp_path / "cutover-no-contract.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        _delete_artifact_governance(connection)
        connection.execute(
            "INSERT INTO board_artifact_cutover (singleton,cutover_state,source_schema_version,"
            "migration_plan_sha256,source_row_set_sha256,created_at_us,completed_at_us) "
            "VALUES (1,'completed',5,?,?,?,?)",
            ("a" * 64, "b" * 64, NOW_US, NOW_US),
        )
        connection.commit()
        assert (
            connection.execute("SELECT version FROM schema_metadata WHERE singleton=1").fetchone()[
                0
            ]
            == 5
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM board_task_artifact_contracts").fetchone()[0]
            == 0
        )
        _record_pre_cutover_asset(connection)
        operations = TaskOperationWriter(
            connection,
            clock_us=MutableClock(now_us=_SUBMITTED_AT_US),
            deadline_actor_id="system-task-board-deadline",
            platform_public_key="ed25519:test-platform-authority",
            current_task_board_caller_kid=BOARD_KID,
        )
        request = _protocol_v1_request(11)
        before = _snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            operations.claim_operation(request, BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")
        assert _snapshot(connection) == before
    finally:
        connection.close()

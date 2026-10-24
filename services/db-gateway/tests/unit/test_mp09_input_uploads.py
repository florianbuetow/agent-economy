"""T-126 red proof for scoped protocol-V2 prepublication input uploads."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from db_gateway_service.task_time import epoch_us_to_utc_text
from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    FREEZE_ID,
    NOW_US,
    POSTER_ID,
    PUBLICATION_ID,
    TASK_ID,
    admission_payload,
    canonical_json,
    canonical_sha256,
    freeze_payload,
    full_snapshot,
    open_prototype_database,
    require_prepublication_api,
    seed_poster,
)
from tests.fakes.mp09_input_publication_fixture import (
    make_writer as make_prepublication_writer,
)
from tests.fakes.mp09_input_upload_fixture import (
    INPUT_BODY_A,
    INPUT_BODY_B,
    INTERNAL_RESERVE_RESOURCE,
    MAX_UPLOAD_DURATION_SECONDS,
    InputClock,
    assert_worker_v1_canonical_unchanged,
    input_upload_payload,
    make_artifact_writer,
    parse_scoped_request,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_MANIFEST_ID = "manifest-input-upload-scope"


def _open_database(tmp_path: Path) -> sqlite3.Connection:
    connection = open_prototype_database(tmp_path / "input-uploads.sqlite3")
    seed_poster(connection)
    return connection


def _seed_input_scope(connection: sqlite3.Connection, expected_count: int) -> str:
    api = require_prepublication_api()
    request = api.requests.parse_publication_admission_v2(
        admission_payload(expected_input_count=expected_count)
    )
    connection.execute(
        "INSERT INTO board_task_publications (operation_id,publication_contract_version,"
        "task_id,canonical_request_json,request_sha256,caller_kid,actor_id,lock_operation_id,"
        "state,admitted_at_us,request_protocol_version,expected_input_count,"
        "base_request_sha256) VALUES (?,?,?,?,?,?,?,?,'awaiting_inputs',?,2,?,?)",
        (
            PUBLICATION_ID,
            1,
            TASK_ID,
            request.canonical_request_json,
            request.request_sha256,
            BOARD_KID,
            POSTER_ID,
            request.lock_operation_id,
            NOW_US,
            expected_count,
            request.base_request_sha256,
        ),
    )
    connection.execute(
        "INSERT INTO board_artifact_manifests (manifest_id,task_id,kind,actor_id,"
        "format_version,state,canonical_manifest_json,manifest_sha256,frozen_at_us,"
        "publication_operation_id,public_task_id) "
        "VALUES (?,?,'input',?,1,'draft',NULL,NULL,NULL,?,NULL)",
        (_MANIFEST_ID, TASK_ID, POSTER_ID, PUBLICATION_ID),
    )
    connection.commit()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    return _MANIFEST_ID


def _request(
    *,
    operation_number: int,
    manifest_id: str,
    body: bytes,
    display_filename: str,
    **overrides: object,
) -> Any:
    return parse_scoped_request(
        input_upload_payload(
            operation_number=operation_number,
            manifest_id=manifest_id,
            body=body,
            display_filename=display_filename,
            **overrides,
        )
    )


def _fence(reservation: dict[str, Any]) -> dict[str, object]:
    attempt = reservation["attempt"]
    return {
        "owner_id": attempt["owner_id"],
        "generation": attempt["generation"],
        "attempt_nonce": attempt["attempt_nonce"],
    }


def _finalize(writer: Any, request: Any, reservation: dict[str, Any]) -> dict[str, Any]:
    return writer.finalize_upload(
        request.upload_operation_id,
        **_fence(reservation),
        observed_size_bytes=request.declared_size_bytes,
        observed_sha256=request.content_sha256,
        verified_caller_kid=BOARD_KID,
    )


def _service_error(call: Any) -> ServiceError:
    with pytest.raises(ServiceError) as raised:
        call()
    return raised.value


def test_scoped_input_request_is_strict_and_worker_v1_canonical_bytes_are_unchanged() -> None:
    assert_worker_v1_canonical_unchanged()
    payload = input_upload_payload(
        operation_number=1,
        manifest_id=_MANIFEST_ID,
        body=INPUT_BODY_A,
        display_filename="input.txt",
    )
    assert set(payload) == {
        "protocol_version",
        "upload_operation_id",
        "resource",
        "manifest_kind",
        "publication_operation_id",
        "task_id",
        "manifest_id",
        "actor_id",
        "actor_role",
        "display_filename",
        "evidence_format",
        "declared_size_bytes",
        "content_sha256",
    }
    assert payload["resource"] != INTERNAL_RESERVE_RESOURCE
    assert canonical_sha256(payload) == hashlib.sha256(canonical_json(payload).encode()).hexdigest()
    request = parse_scoped_request(payload)
    assert request.canonical_json == canonical_json(payload)
    assert request.request_sha256 == canonical_sha256(payload)

    for missing in payload:
        incomplete = dict(payload)
        del incomplete[missing]
        with pytest.raises(ServiceError) as raised:
            parse_scoped_request(incomplete)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")
    for worker_field in (
        "expected_lifecycle_version",
        "accepted_binding_kind",
        "accepted_binding_sha256",
    ):
        mixed = dict(payload)
        mixed[worker_field] = None
        with pytest.raises(ServiceError) as raised:
            parse_scoped_request(mixed)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("unexpected", "field"),
        ("protocol_version", 1),
        ("protocol_version", "2"),
        ("resource", "POST /task-publications/wrong/inputs"),
        ("manifest_kind", "submission"),
        ("actor_role", "worker"),
        ("declared_size_bytes", "12"),
        ("publication_operation_id", "80000000-0000-4000-8000-000000000001"),
        ("publication_operation_id", "op-80000000-0000-4000-8000-000000000099"),
        ("content_sha256", "A" * 64),
        ("content_sha256", "a" * 63),
    ],
)
def test_scoped_input_request_rejects_wrong_protocol_scope_tags_and_types(
    field: str,
    value: object,
) -> None:
    payload = input_upload_payload(
        operation_number=1,
        manifest_id=_MANIFEST_ID,
        body=INPUT_BODY_A,
        display_filename="input.txt",
    )
    payload[field] = value
    with pytest.raises(ServiceError) as raised:
        parse_scoped_request(payload)
    assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")


def test_admission_accepts_positive_expected_input_count_within_policy(tmp_path: Path) -> None:
    connection = _open_database(tmp_path)
    try:
        api = require_prepublication_api()
        writer = make_prepublication_writer(api, connection)
        request = api.requests.parse_publication_admission_v2(
            admission_payload(expected_input_count=2)
        )
        admitted = writer.admit(request, verified_caller_kid=BOARD_KID)
        assert admitted["publication_state"] == "awaiting_inputs"
        assert admitted["expected_input_count"] == 2
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
    finally:
        connection.close()


def test_input_reserves_before_public_task_exists(tmp_path: Path) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 1)
        clock = InputClock()
        writer = make_artifact_writer(connection, clock)
        request = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="input.txt",
        )
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
        reserved = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        assert reserved["state"] == "reserved"
        assert reserved["manifest_kind"] == "input"
        assert reserved["publication_operation_id"] == PUBLICATION_ID
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
    finally:
        connection.close()


def test_zero_expected_inputs_rejects_reservation_without_mutation(tmp_path: Path) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 0)
        writer = make_artifact_writer(connection, InputClock())
        request = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="input.txt",
        )
        before = full_snapshot(connection)
        error = _service_error(
            lambda: writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        )
        assert (error.status_code, error.error) == (409, "input_count_exceeded")
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_reserved_and_ready_uploads_consume_exact_expected_count_slots(tmp_path: Path) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 2)
        clock = InputClock()
        writer = make_artifact_writer(connection, clock)
        first = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="alpha.txt",
        )
        _finalize(writer, first, writer.reserve_upload(first, verified_caller_kid=BOARD_KID))
        second = _request(
            operation_number=2,
            manifest_id=manifest_id,
            body=INPUT_BODY_B,
            display_filename="beta.txt",
        )
        writer.reserve_upload(second, verified_caller_kid=BOARD_KID)
        third = _request(
            operation_number=3,
            manifest_id=manifest_id,
            body=b"third\n",
            display_filename="third.txt",
        )
        before = full_snapshot(connection)
        error = _service_error(lambda: writer.reserve_upload(third, verified_caller_kid=BOARD_KID))
        assert (error.status_code, error.error) == (409, "input_count_exceeded")
        assert full_snapshot(connection) == before
        rows = connection.execute(
            "SELECT state,COUNT(*) FROM board_artifact_uploads GROUP BY state ORDER BY state"
        ).fetchall()
        assert [tuple(row) for row in rows] == [("ready", 1), ("reserved", 1)]
    finally:
        connection.close()


def test_exact_replay_and_expired_takeover_reuse_one_input_slot(tmp_path: Path) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 1)
        clock = InputClock()
        writer = make_artifact_writer(connection, clock)
        request = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="input.txt",
        )
        first = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        before = full_snapshot(connection)
        pending = _service_error(
            lambda: writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        )
        assert (pending.status_code, pending.error) == (409, "upload_pending")
        assert full_snapshot(connection) == before

        clock.now_us = int(first["attempt"]["lease_expires_at_us"])
        takeover = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        assert takeover["attempt"]["generation"] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_artifact_uploads WHERE manifest_id=?",
                (manifest_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_artifact_upload_attempts WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()[0]
            == 2
        )
    finally:
        connection.close()


@pytest.mark.parametrize("terminal_action", ["renew", "finalize"])
def test_renew_and_finalize_reject_exact_lease_expiry_without_mutation_or_task_read(
    tmp_path: Path,
    terminal_action: str,
) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 1)
        clock = InputClock()
        writer = make_artifact_writer(connection, clock)
        request = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="input.txt",
        )
        reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        clock.now_us = int(reservation["attempt"]["lease_expires_at_us"])
        before = full_snapshot(connection)

        def deny_task_read(
            action: int,
            table: str | None,
            _column: str | None,
            _database: str | None,
            _source: str | None,
        ) -> int:
            if action == sqlite3.SQLITE_READ and table == "board_tasks":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_task_read)
        try:
            if terminal_action == "renew":
                error = _service_error(
                    lambda: writer.renew_upload(
                        request.upload_operation_id,
                        **_fence(reservation),
                        verified_caller_kid=BOARD_KID,
                    )
                )
            else:
                error = _service_error(lambda: _finalize(writer, request, reservation))
            assert (error.status_code, error.error) == (409, "upload_lease_expired")
        finally:
            connection.set_authorizer(None)
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_orphan_remains_explicitly_available_at_exact_lease_expiry(tmp_path: Path) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 1)
        clock = InputClock()
        writer = make_artifact_writer(connection, clock)
        request = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="input.txt",
        )
        reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        clock.now_us = int(reservation["attempt"]["lease_expires_at_us"])
        result = writer.orphan_upload(
            request.upload_operation_id,
            **_fence(reservation),
            failure_code="client_disconnected",
            verified_caller_kid=BOARD_KID,
        )
        assert result["state"] == "orphaned"
    finally:
        connection.close()


def test_input_reservation_deadline_uses_configured_duration_without_task_cutoff(
    tmp_path: Path,
) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 1)
        clock = InputClock()
        writer = make_artifact_writer(connection, clock)
        request = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="input.txt",
        )
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
        reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        assert reservation["attempt"]["absolute_upload_deadline_us"] == (
            clock.now_us + MAX_UPLOAD_DURATION_SECONDS * 1_000_000
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "value", "expected_status", "expected_error"),
    [
        ("actor_id", "poster-other", 403, "forbidden"),
        (
            "publication_operation_id",
            "op-80000000-0000-4000-8000-000000000099",
            404,
            "publication_not_found",
        ),
        ("task_id", "t-80000000-0000-4000-8000-000000000099", 409, "publication_mismatch"),
        ("manifest_id", "manifest-unknown", 404, "manifest_not_found"),
        ("existing_publication", "unused", 409, "publication_mismatch"),
        ("existing_manifest", "unused", 409, "manifest_mismatch"),
    ],
)
def test_input_reservation_scope_mismatch_has_no_effect(
    tmp_path: Path,
    field: str,
    value: object,
    expected_status: int,
    expected_error: str,
) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 1)
        connection.execute(
            "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) VALUES (?,?,?,?)",
            ("poster-other", "Other", "ed25519:other", "2099-01-01T23:58:01Z"),
        )
        connection.commit()
        overrides: dict[str, object] = {field: value}
        if field in {"existing_publication", "existing_manifest"}:
            api = require_prepublication_api()
            prepublication = make_prepublication_writer(api, connection)
            alternate_publication = "op-80000000-0000-4000-8000-000000000098"
            alternate_task = "t-80000000-0000-4000-8000-000000000098"
            alternate_payload = admission_payload(
                expected_input_count=0,
                publication_operation_id=alternate_publication,
                task_id=alternate_task,
                lock_operation_id=operation_id_for(
                    "task-lock",
                    (alternate_task, POSTER_ID),
                ),
            )
            alternate_request = api.requests.parse_publication_admission_v2(alternate_payload)
            alternate = prepublication.admit(
                alternate_request,
                verified_caller_kid=BOARD_KID,
            )
            if field == "existing_publication":
                overrides = {
                    "publication_operation_id": alternate_publication,
                    "resource": f"POST /task-publications/{alternate_publication}/inputs",
                }
            else:
                overrides = {"manifest_id": alternate["input_manifest_id"]}
        if field == "publication_operation_id":
            overrides["resource"] = f"POST /task-publications/{value}/inputs"
        request_manifest_id = str(overrides.pop("manifest_id", manifest_id))
        request = _request(
            operation_number=1,
            manifest_id=request_manifest_id,
            body=INPUT_BODY_A,
            display_filename="input.txt",
            **overrides,
        )
        writer = make_artifact_writer(connection, InputClock())
        before = full_snapshot(connection)
        error = _service_error(
            lambda: writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        )
        assert (error.status_code, error.error) == (expected_status, expected_error)
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def _ready_manifest_value(
    connection: sqlite3.Connection,
    manifest_id: str,
) -> dict[str, object]:
    ready_rows = connection.execute(
        "SELECT artifact_id,display_filename,evidence_format,media_type,"
        "declared_size_bytes,content_sha256,actor_id,finalized_at_us "
        "FROM board_artifact_uploads WHERE manifest_id=? AND state='ready' "
        "ORDER BY artifact_id",
        (manifest_id,),
    ).fetchall()
    return {
        "format_version": 1,
        "manifest_id": manifest_id,
        "task_id": TASK_ID,
        "kind": "input",
        "actor_id": POSTER_ID,
        "artifacts": [
            {
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
            for row in ready_rows
        ],
    }


def _force_adversarial_freeze(
    connection: sqlite3.Connection,
    manifest_id: str,
) -> tuple[str, str]:
    """Freeze valid ready membership while deliberately retaining one active attempt."""
    manifest_value = _ready_manifest_value(connection, manifest_id)
    canonical_manifest = canonical_json(manifest_value)
    manifest_sha256 = canonical_sha256(manifest_value)
    publication = connection.execute(
        "SELECT base_request_sha256 FROM board_task_publications WHERE operation_id=?",
        (PUBLICATION_ID,),
    ).fetchone()
    assert publication is not None
    publication_contract_sha256 = canonical_sha256(
        {
            "artifact_contract_version": 1,
            "base_request_sha256": str(publication[0]),
            "input_manifest_sha256": manifest_sha256,
        }
    )
    api = require_prepublication_api()
    freeze_request = _freeze_request(api, manifest_id)
    connection.execute(
        "UPDATE board_artifact_manifests SET state='frozen',canonical_manifest_json=?,"
        "manifest_sha256=?,frozen_at_us=? WHERE manifest_id=?",
        (canonical_manifest, manifest_sha256, NOW_US, manifest_id),
    )
    connection.execute(
        "UPDATE board_task_publications SET state='awaiting_publish',freeze_operation_id=?,"
        "canonical_freeze_request_json=?,freeze_request_sha256=?,"
        "input_manifest_sha256=?,publication_contract_sha256=?,inputs_frozen_at_us=? "
        "WHERE operation_id=?",
        (
            FREEZE_ID,
            freeze_request.canonical_freeze_request_json,
            freeze_request.freeze_request_sha256,
            manifest_sha256,
            publication_contract_sha256,
            NOW_US,
            PUBLICATION_ID,
        ),
    )
    connection.commit()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    return manifest_sha256, publication_contract_sha256


def test_frozen_publication_loss_durably_orphans_current_attempt(tmp_path: Path) -> None:
    """Adversarially freeze exact ready membership while a second attempt remains active."""
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 2)
        writer = make_artifact_writer(connection, InputClock())
        ready_request = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="ready.txt",
        )
        _finalize(
            writer,
            ready_request,
            writer.reserve_upload(ready_request, verified_caller_kid=BOARD_KID),
        )
        request = _request(
            operation_number=2,
            manifest_id=manifest_id,
            body=INPUT_BODY_B,
            display_filename="active.txt",
        )
        reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        _force_adversarial_freeze(connection, manifest_id)
        ready_before = tuple(
            connection.execute(
                "SELECT * FROM board_artifact_uploads WHERE upload_operation_id=?",
                (ready_request.upload_operation_id,),
            ).fetchone()
            or ()
        )
        ready_attempts_before = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM board_artifact_upload_attempts WHERE upload_operation_id=? "
                "ORDER BY generation",
                (ready_request.upload_operation_id,),
            ).fetchall()
        ]
        manifest_before = tuple(
            connection.execute(
                "SELECT * FROM board_artifact_manifests WHERE manifest_id=?",
                (manifest_id,),
            ).fetchone()
            or ()
        )
        publication_before = tuple(
            connection.execute(
                "SELECT * FROM board_task_publications WHERE operation_id=?",
                (PUBLICATION_ID,),
            ).fetchone()
            or ()
        )
        error = _service_error(lambda: _finalize(writer, request, reservation))
        assert (error.status_code, error.error) == (409, "invalid_status")
        upload = connection.execute(
            "SELECT state,current_attempt_id,finalized_at_us FROM board_artifact_uploads "
            "WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        attempt = connection.execute(
            "SELECT state,observed_size_bytes,observed_sha256,failure_code,updated_at_us "
            "FROM board_artifact_upload_attempts WHERE attempt_id=?",
            (reservation["attempt"]["attempt_id"],),
        ).fetchone()
        assert upload is not None and tuple(upload) == ("orphaned", None, NOW_US)
        assert attempt is not None and tuple(attempt) == (
            "orphaned",
            None,
            None,
            "invalid_status",
            NOW_US,
        )
        assert (
            tuple(
                connection.execute(
                    "SELECT * FROM board_artifact_uploads WHERE upload_operation_id=?",
                    (ready_request.upload_operation_id,),
                ).fetchone()
                or ()
            )
            == ready_before
        )
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM board_artifact_upload_attempts WHERE upload_operation_id=? "
                "ORDER BY generation",
                (ready_request.upload_operation_id,),
            ).fetchall()
        ] == ready_attempts_before
        assert (
            tuple(
                connection.execute(
                    "SELECT * FROM board_artifact_manifests WHERE manifest_id=?",
                    (manifest_id,),
                ).fetchone()
                or ()
            )
            == manifest_before
        )
        assert (
            tuple(
                connection.execute(
                    "SELECT * FROM board_task_publications WHERE operation_id=?",
                    (PUBLICATION_ID,),
                ).fetchone()
                or ()
            )
            == publication_before
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_expired_input_takeover_after_publication_state_loss_has_no_effect(
    tmp_path: Path,
) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 2)
        clock = InputClock()
        writer = make_artifact_writer(connection, clock)
        ready_request = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="ready.txt",
        )
        _finalize(
            writer,
            ready_request,
            writer.reserve_upload(ready_request, verified_caller_kid=BOARD_KID),
        )
        request = _request(
            operation_number=2,
            manifest_id=manifest_id,
            body=INPUT_BODY_B,
            display_filename="active.txt",
        )
        reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        _force_adversarial_freeze(connection, manifest_id)
        clock.now_us = int(reservation["attempt"]["lease_expires_at_us"])
        before = full_snapshot(connection)
        error = _service_error(
            lambda: writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        )
        assert (error.status_code, error.error) == (409, "invalid_status")
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def _freeze_request(api: Any, manifest_id: str) -> Any:
    return api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))


def test_nonempty_freeze_persists_exact_sorted_ready_manifest(tmp_path: Path) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 2)
        clock = InputClock()
        writer = make_artifact_writer(connection, clock)
        requests = [
            _request(
                operation_number=1,
                manifest_id=manifest_id,
                body=INPUT_BODY_A,
                display_filename="zeta.txt",
            ),
            _request(
                operation_number=2,
                manifest_id=manifest_id,
                body=INPUT_BODY_B,
                display_filename="alpha.txt",
            ),
        ]
        for request in requests:
            reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
            _finalize(writer, request, reservation)
        ready_before = [
            tuple(row)
            for row in connection.execute(
                "SELECT * FROM board_artifact_uploads WHERE manifest_id=? AND state='ready' "
                "ORDER BY artifact_id",
                (manifest_id,),
            ).fetchall()
        ]
        expected_manifest = _ready_manifest_value(connection, manifest_id)
        expected_manifest_json = canonical_json(expected_manifest)
        expected_manifest_sha256 = canonical_sha256(expected_manifest)
        base_request = connection.execute(
            "SELECT base_request_sha256 FROM board_task_publications WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert base_request is not None
        expected_contract_sha256 = canonical_sha256(
            {
                "artifact_contract_version": 1,
                "base_request_sha256": str(base_request[0]),
                "input_manifest_sha256": expected_manifest_sha256,
            }
        )
        api = require_prepublication_api()
        prepublication = make_prepublication_writer(api, connection)
        freeze_request = _freeze_request(api, manifest_id)
        frozen = prepublication.freeze_inputs(
            freeze_request,
            verified_caller_kid=BOARD_KID,
        )
        row = connection.execute(
            "SELECT state,canonical_manifest_json,manifest_sha256,frozen_at_us "
            "FROM board_artifact_manifests "
            "WHERE manifest_id=?",
            (manifest_id,),
        ).fetchone()
        assert row is not None
        assert tuple(row) == (
            "frozen",
            expected_manifest_json,
            expected_manifest_sha256,
            NOW_US,
        )
        decoded = json.loads(str(row[1]))
        artifact_ids = [str(item["artifact_id"]) for item in decoded["artifacts"]]
        assert artifact_ids == sorted(artifact_ids)
        assert len(artifact_ids) == 2
        assert decoded == expected_manifest
        assert str(row[2]) == hashlib.sha256(str(row[1]).encode("utf-8")).hexdigest()
        publication = connection.execute(
            "SELECT state,freeze_operation_id,canonical_freeze_request_json,"
            "freeze_request_sha256,input_manifest_sha256,publication_contract_sha256,"
            "inputs_frozen_at_us FROM board_task_publications WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert publication is not None
        assert tuple(publication) == (
            "awaiting_publish",
            FREEZE_ID,
            freeze_request.canonical_freeze_request_json,
            freeze_request.freeze_request_sha256,
            expected_manifest_sha256,
            expected_contract_sha256,
            NOW_US,
        )
        assert frozen["publication_state"] == "awaiting_publish"
        assert frozen["freeze_operation_id"] == FREEZE_ID
        assert frozen["input_manifest_id"] == manifest_id
        assert frozen["input_manifest_sha256"] == expected_manifest_sha256
        assert frozen["publication_contract_sha256"] == expected_contract_sha256
        assert [
            tuple(upload)
            for upload in connection.execute(
                "SELECT * FROM board_artifact_uploads WHERE manifest_id=? AND state='ready' "
                "ORDER BY artifact_id",
                (manifest_id,),
            ).fetchall()
        ] == ready_before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize("state", ["pending", "missing"])
def test_nonempty_freeze_rejects_pending_or_missing_expected_inputs(
    tmp_path: Path,
    state: str,
) -> None:
    connection = _open_database(tmp_path)
    try:
        manifest_id = _seed_input_scope(connection, 2)
        writer = make_artifact_writer(connection, InputClock())
        first = _request(
            operation_number=1,
            manifest_id=manifest_id,
            body=INPUT_BODY_A,
            display_filename="alpha.txt",
        )
        _finalize(writer, first, writer.reserve_upload(first, verified_caller_kid=BOARD_KID))
        if state == "pending":
            second = _request(
                operation_number=2,
                manifest_id=manifest_id,
                body=INPUT_BODY_B,
                display_filename="beta.txt",
            )
            writer.reserve_upload(second, verified_caller_kid=BOARD_KID)
        api = require_prepublication_api()
        prepublication = make_prepublication_writer(api, connection)
        before = full_snapshot(connection)
        error = _service_error(
            lambda: prepublication.freeze_inputs(
                _freeze_request(api, manifest_id),
                verified_caller_kid=BOARD_KID,
            )
        )
        expected = (409, "upload_pending") if state == "pending" else (409, "input_count_mismatch")
        assert (error.status_code, error.error) == expected
        assert full_snapshot(connection) == before
    finally:
        connection.close()

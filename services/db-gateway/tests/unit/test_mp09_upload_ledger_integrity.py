"""Behavioral reds for worker upload journal and attempt-ledger integrity."""

from __future__ import annotations

import sqlite3
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import parse_artifact_upload_request
from db_gateway_service.services.artifact_writer import ArtifactWriter
from tests.fakes.mp09_v6_artifact_fixture import (
    BOARD_KID,
    BODY_A,
    BODY_B,
    HASH_A,
    HASH_B,
    TASK_ID,
    MutableClock,
    attempt_fence,
    database_snapshot,
    open_v6_database,
    seed_accepted_task,
    upload_payload,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.unit

_TASK_ID = TASK_ID
_MANIFEST_ID = "manifest-v6-reservation"
_NEXT_KID = "gateway-task-board-next"


def _operation(number: int) -> str:
    return f"90000000-0000-4000-8000-{number:012d}"


def _policy() -> Any:
    from db_gateway_service.artifact_upload_requests import parse_artifact_policy

    return parse_artifact_policy(
        {
            "contract_version": 1,
            "max_file_bytes": 32,
            "max_files_per_manifest": 2,
            "max_manifest_bytes": 64,
            "reservation_ttl_seconds": 300,
            "reservation_renew_before_expiry_seconds": 60,
            "max_upload_duration_seconds": 900,
        }
    )


def _writer(connection: sqlite3.Connection, clock: MutableClock, kid: str) -> ArtifactWriter:
    return ArtifactWriter(
        connection,
        clock_us=clock,
        policy=_policy(),
        current_task_board_caller_kid=kid,
    )


def _request(
    number: int,
    *,
    body: bytes = BODY_A,
    content_sha256: str = HASH_A,
    display_filename: str = "answer.txt",
    **overrides: object,
) -> Any:
    payload = upload_payload(
        operation_id=_operation(number),
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
        body=body,
        content_sha256=content_sha256,
        display_filename=display_filename,
    )
    payload.update(overrides)
    return parse_artifact_upload_request(payload)


def _reserve(writer: ArtifactWriter, request: Any, kid: str = BOARD_KID) -> dict[str, Any]:
    return writer.reserve_upload(request, verified_caller_kid=kid)


def _finalize(
    writer: ArtifactWriter,
    request: Any,
    reservation: dict[str, Any],
    kid: str = BOARD_KID,
) -> dict[str, Any]:
    return writer.finalize_upload(
        request.upload_operation_id,
        **attempt_fence(reservation),
        observed_size_bytes=request.declared_size_bytes,
        observed_sha256=request.content_sha256,
        verified_caller_kid=kid,
    )


def _assert_error(
    call: Callable[[], object],
    connection: sqlite3.Connection,
    expected: tuple[int, str],
) -> None:
    before = database_snapshot(connection)
    with pytest.raises(ServiceError) as raised:
        call()
    assert (raised.value.status_code, raised.value.error) == expected
    assert database_snapshot(connection) == before


def test_valid_worker_upload_control_finalizes_and_replays_one_artifact(tmp_path: Path) -> None:
    connection = open_v6_database(tmp_path / "valid-worker-control.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        clock = MutableClock()
        writer = _writer(connection, clock, BOARD_KID)
        request = _request(1)
        reserved = _reserve(writer, request)
        ready = _finalize(writer, request, reserved)
        assert ready["state"] == "ready"
        assert _reserve(writer, request) == ready
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    "operation",
    [
        "active_replay",
        "expired_takeover",
        "ready_replay",
        "orphaned_replay",
        "renew",
        "finalize",
        "orphan",
    ],
)
def test_reconfigured_writer_cannot_use_upload_owned_by_prior_caller(
    tmp_path: Path,
    operation: str,
) -> None:
    connection = open_v6_database(tmp_path / f"caller-{operation}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        clock = MutableClock()
        original = _writer(connection, clock, BOARD_KID)
        request = _request(1)
        reservation = _reserve(original, request)
        if operation == "expired_takeover":
            clock.now_us = int(reservation["attempt"]["lease_expires_at_us"])
        elif operation == "ready_replay":
            _finalize(original, request, reservation)
        elif operation == "orphaned_replay":
            original.orphan_upload(
                request.upload_operation_id,
                **attempt_fence(reservation),
                failure_code="client_disconnected",
                verified_caller_kid=BOARD_KID,
            )
        replacement = _writer(connection, clock, _NEXT_KID)
        if operation in {
            "active_replay",
            "expired_takeover",
            "ready_replay",
            "orphaned_replay",
        }:
            call = partial(_reserve, replacement, request, _NEXT_KID)
        elif operation == "renew":
            call = partial(
                replacement.renew_upload,
                request.upload_operation_id,
                **attempt_fence(reservation),
                verified_caller_kid=_NEXT_KID,
            )
        elif operation == "finalize":
            call = partial(_finalize, replacement, request, reservation, _NEXT_KID)
        else:
            call = partial(
                replacement.orphan_upload,
                request.upload_operation_id,
                **attempt_fence(reservation),
                failure_code="client_disconnected",
                verified_caller_kid=_NEXT_KID,
            )
        _assert_error(call, connection, (403, "artifact_caller_mismatch"))
    finally:
        connection.close()


def _tamper_upload(connection: sqlite3.Connection, tamper: str, operation_id: str) -> None:
    updates: dict[str, object] = {
        "canonical": '{"tampered":true}',
        "request_hash": "f" * 64,
        "display_filename": "tampered.txt",
        "evidence_format": "json",
        "declared_size_bytes": 1,
        "content_sha256": "f" * 64,
        "expected_lifecycle_version": 2,
        "accepted_binding_kind": "legacy_accepted_continuation",
        "accepted_binding_sha256": "f" * 64,
    }
    column = "canonical_request_json" if tamper == "canonical" else tamper
    if tamper == "request_hash":
        column = "request_sha256"
    connection.execute(
        f"UPDATE board_artifact_uploads SET {column}=? WHERE upload_operation_id=?",  # nosec B608
        (updates[tamper], operation_id),
    )
    connection.commit()


@pytest.mark.parametrize(
    "tamper", ["canonical", "request_hash", "declared_size_bytes", "content_sha256"]
)
@pytest.mark.parametrize("operation", ["replay", "takeover", "renew", "finalize", "orphan"])
def test_upload_immutable_tampering_fails_closed_across_all_operation_paths(
    tmp_path: Path,
    tamper: str,
    operation: str,
) -> None:
    connection = open_v6_database(tmp_path / f"tamper-{tamper}-{operation}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        clock = MutableClock()
        writer = _writer(connection, clock, BOARD_KID)
        request = _request(1)
        reservation = _reserve(writer, request)
        _tamper_upload(connection, tamper, request.upload_operation_id)
        if operation == "takeover":
            clock.now_us = int(reservation["attempt"]["lease_expires_at_us"])
        if operation in {"replay", "takeover"}:
            call = partial(_reserve, writer, request)
        elif operation == "renew":
            call = partial(
                writer.renew_upload,
                request.upload_operation_id,
                **attempt_fence(reservation),
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "finalize":
            call = partial(_finalize, writer, request, reservation)
        else:
            call = partial(
                writer.orphan_upload,
                request.upload_operation_id,
                **attempt_fence(reservation),
                failure_code="client_disconnected",
                verified_caller_kid=BOARD_KID,
            )
        _assert_error(call, connection, (500, "artifact_state_corrupt"))
    finally:
        connection.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "display_filename",
        "evidence_format",
        "expected_lifecycle_version",
        "accepted_binding_kind",
        "accepted_binding_sha256",
    ],
)
def test_other_denormalized_identity_and_binding_tampering_fails_replay(
    tmp_path: Path,
    tamper: str,
) -> None:
    connection = open_v6_database(tmp_path / f"projection-{tamper}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        writer = _writer(connection, MutableClock(), BOARD_KID)
        request = _request(1)
        _reserve(writer, request)
        _tamper_upload(connection, tamper, request.upload_operation_id)
        _assert_error(
            partial(_reserve, writer, request),
            connection,
            (500, "artifact_state_corrupt"),
        )
    finally:
        connection.close()


def test_intact_upload_with_changed_incoming_intent_keeps_operation_conflict(
    tmp_path: Path,
) -> None:
    connection = open_v6_database(tmp_path / "changed-intent-control.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        writer = _writer(connection, MutableClock(), BOARD_KID)
        original = _request(1)
        _reserve(writer, original)
        changed = _request(1, display_filename="changed.txt")
        _assert_error(
            partial(_reserve, writer, changed),
            connection,
            (409, "upload_operation_conflict"),
        )
    finally:
        connection.close()


def _two_reservations(
    connection: sqlite3.Connection,
) -> tuple[MutableClock, ArtifactWriter, Any, dict[str, Any], Any, dict[str, Any]]:
    clock = MutableClock()
    writer = _writer(connection, clock, BOARD_KID)
    first_request = _request(1)
    first = _reserve(writer, first_request)
    second_request = _request(
        2,
        body=BODY_B,
        content_sha256=HASH_B,
        display_filename="second.txt",
    )
    second = _reserve(writer, second_request)
    return clock, writer, first_request, first, second_request, second


def test_schema_rejects_cross_upload_current_attempt_linkage(tmp_path: Path) -> None:
    connection = open_v6_database(tmp_path / "cross-link-schema.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        _clock, _writer_value, first_request, _first, _second_request, second = _two_reservations(
            connection
        )
        before = database_snapshot(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE board_artifact_uploads SET current_attempt_id=? "
                "WHERE upload_operation_id=?",
                (second["attempt"]["attempt_id"], first_request.upload_operation_id),
            )
        connection.rollback()
        assert database_snapshot(connection) == before
    finally:
        connection.close()


def _inject_cross_upload_attempt_corruption(
    connection: sqlite3.Connection,
    first_operation_id: str,
    second_attempt_id: str,
) -> None:
    """Build an impossible runtime row with FK enforcement briefly disabled."""
    connection.commit()
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "UPDATE board_artifact_uploads SET current_attempt_id=? WHERE upload_operation_id=?",
        (second_attempt_id, first_operation_id),
    )
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


@pytest.mark.parametrize("operation", ["replay", "takeover", "finalize"])
def test_runtime_cross_upload_attempt_corruption_fails_closed(
    tmp_path: Path,
    operation: str,
) -> None:
    connection = open_v6_database(tmp_path / f"cross-link-runtime-{operation}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        clock, writer, first_request, _first, _second_request, second = _two_reservations(
            connection
        )
        _inject_cross_upload_attempt_corruption(
            connection,
            first_request.upload_operation_id,
            str(second["attempt"]["attempt_id"]),
        )
        if operation == "takeover":
            clock.now_us = int(second["attempt"]["lease_expires_at_us"])
        if operation in {"replay", "takeover"}:
            call = partial(_reserve, writer, first_request)
        else:
            call = partial(
                writer.finalize_upload,
                first_request.upload_operation_id,
                **attempt_fence(second),
                observed_size_bytes=first_request.declared_size_bytes,
                observed_sha256=first_request.content_sha256,
                verified_caller_kid=BOARD_KID,
            )
        _assert_error(call, connection, (500, "artifact_state_corrupt"))
    finally:
        connection.close()


@pytest.mark.parametrize("operation", ["replay", "finalize"])
def test_attempt_intended_object_key_tampering_fails_closed(
    tmp_path: Path,
    operation: str,
) -> None:
    connection = open_v6_database(tmp_path / f"attempt-object-{operation}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        writer = _writer(connection, MutableClock(), BOARD_KID)
        request = _request(1)
        reservation = _reserve(writer, request)
        connection.execute(
            "UPDATE board_artifact_upload_attempts SET intended_object_key=? WHERE attempt_id=?",
            ("live/ff/tampered.blob", reservation["attempt"]["attempt_id"]),
        )
        connection.commit()
        call = (
            partial(_reserve, writer, request)
            if operation == "replay"
            else partial(_finalize, writer, request, reservation)
        )
        _assert_error(call, connection, (500, "artifact_state_corrupt"))
    finally:
        connection.close()


@pytest.mark.parametrize(
    "boundary",
    [
        "renew_attempt",
        "takeover_old_attempt",
        "takeover_parent",
        "finalize_attempt",
        "finalize_parent",
        "orphan_attempt",
        "orphan_parent",
    ],
)
def test_ignored_ledger_write_is_persistence_failure_with_full_rollback(
    tmp_path: Path,
    boundary: str,
) -> None:
    connection = open_v6_database(tmp_path / f"ignore-{boundary}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=_TASK_ID, manifest_id=_MANIFEST_ID)
        clock = MutableClock()
        writer = _writer(connection, clock, BOARD_KID)
        request = _request(1)
        reservation = _reserve(writer, request)
        attempt_id = str(reservation["attempt"]["attempt_id"])
        if boundary == "renew_attempt":
            trigger = (
                "CREATE TRIGGER ignore_renew BEFORE UPDATE OF lease_expires_at_us "
                "ON board_artifact_upload_attempts BEGIN SELECT RAISE(IGNORE); END"
            )
            call = partial(
                writer.renew_upload,
                request.upload_operation_id,
                **attempt_fence(reservation),
                verified_caller_kid=BOARD_KID,
            )
        elif boundary.startswith("takeover"):
            clock.now_us = int(reservation["attempt"]["lease_expires_at_us"])
            if boundary == "takeover_old_attempt":
                trigger = (
                    "CREATE TRIGGER ignore_takeover_attempt BEFORE UPDATE OF state "
                    "ON board_artifact_upload_attempts WHEN OLD.attempt_id='"
                    + attempt_id
                    + "' BEGIN SELECT RAISE(IGNORE); END"
                )
            else:
                trigger = (
                    "CREATE TRIGGER ignore_takeover_parent BEFORE UPDATE OF current_attempt_id "
                    "ON board_artifact_uploads BEGIN SELECT RAISE(IGNORE); END"
                )
            call = partial(_reserve, writer, request)
        elif boundary.startswith("finalize"):
            if boundary == "finalize_attempt":
                trigger = (
                    "CREATE TRIGGER ignore_finalize_attempt BEFORE UPDATE OF state "
                    "ON board_artifact_upload_attempts BEGIN SELECT RAISE(IGNORE); END"
                )
            else:
                trigger = (
                    "CREATE TRIGGER ignore_finalize_parent BEFORE UPDATE OF state "
                    "ON board_artifact_uploads BEGIN SELECT RAISE(IGNORE); END"
                )
            call = partial(_finalize, writer, request, reservation)
        else:
            if boundary == "orphan_attempt":
                trigger = (
                    "CREATE TRIGGER ignore_orphan_attempt BEFORE UPDATE OF state "
                    "ON board_artifact_upload_attempts BEGIN SELECT RAISE(IGNORE); END"
                )
            else:
                trigger = (
                    "CREATE TRIGGER ignore_orphan_parent BEFORE UPDATE OF state "
                    "ON board_artifact_uploads BEGIN SELECT RAISE(IGNORE); END"
                )
            call = partial(
                writer.orphan_upload,
                request.upload_operation_id,
                **attempt_fence(reservation),
                failure_code="client_disconnected",
                verified_caller_kid=BOARD_KID,
            )
        connection.execute(trigger)
        connection.commit()
        _assert_error(call, connection, (500, "artifact_persistence_failed"))
    finally:
        connection.close()

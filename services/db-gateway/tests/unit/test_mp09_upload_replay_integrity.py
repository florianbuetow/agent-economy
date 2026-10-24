"""Terminal replay and caller-precedence integrity regressions."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import (
    parse_artifact_policy,
    parse_artifact_upload_request,
)
from db_gateway_service.services.artifact_writer import ArtifactWriter
from tests.fakes.mp09_v6_artifact_fixture import (
    BOARD_KID,
    BODY_A,
    HASH_A,
    TASK_ID,
    MutableClock,
    attempt_fence,
    database_snapshot,
    open_v6_database,
    seed_accepted_task,
    upload_payload,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.unit

_MANIFEST_ID = "manifest-v6-reservation"
_NEXT_KID = "gateway-task-board-next"


def _policy() -> Any:
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


def _request(*, changed_filename: bool = False) -> Any:
    payload = upload_payload(
        operation_id="a0000000-0000-4000-8000-000000000001",
        task_id=TASK_ID,
        manifest_id=_MANIFEST_ID,
        body=BODY_A,
        content_sha256=HASH_A,
        display_filename="changed.txt" if changed_filename else "answer.txt",
    )
    return parse_artifact_upload_request(payload)


def _finalize(
    writer: ArtifactWriter,
    request: Any,
    reservation: dict[str, Any],
    kid: str,
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


@pytest.mark.parametrize("terminal_state", ["ready", "orphaned"])
@pytest.mark.parametrize("tamper", ["canonical", "display_filename"])
def test_terminal_exact_replay_revalidates_canonical_and_denormalized_identity(
    tmp_path: Path,
    terminal_state: str,
    tamper: str,
) -> None:
    connection = open_v6_database(tmp_path / f"terminal-{terminal_state}-{tamper}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=TASK_ID, manifest_id=_MANIFEST_ID)
        clock = MutableClock()
        writer = _writer(connection, clock, BOARD_KID)
        request = _request()
        reservation = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        if terminal_state == "ready":
            _finalize(writer, request, reservation, BOARD_KID)
        else:
            writer.orphan_upload(
                request.upload_operation_id,
                **attempt_fence(reservation),
                failure_code="client_disconnected",
                verified_caller_kid=BOARD_KID,
            )
        if tamper == "canonical":
            connection.execute(
                "UPDATE board_artifact_uploads SET canonical_request_json=? "
                "WHERE upload_operation_id=?",
                ('{"tampered":true}', request.upload_operation_id),
            )
        else:
            connection.execute(
                "UPDATE board_artifact_uploads SET display_filename='tampered.txt' "
                "WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            )
        connection.commit()
        _assert_error(
            partial(writer.reserve_upload, request, verified_caller_kid=BOARD_KID),
            connection,
            (500, "artifact_state_corrupt"),
        )
    finally:
        connection.close()


def test_reconfigured_caller_precedes_changed_incoming_intent_conflict(tmp_path: Path) -> None:
    connection = open_v6_database(tmp_path / "caller-changed-intent.sqlite3")
    try:
        seed_accepted_task(connection, task_id=TASK_ID, manifest_id=_MANIFEST_ID)
        clock = MutableClock()
        original_writer = _writer(connection, clock, BOARD_KID)
        original = _request()
        original_writer.reserve_upload(original, verified_caller_kid=BOARD_KID)
        replacement = _writer(connection, clock, _NEXT_KID)
        changed = _request(changed_filename=True)
        _assert_error(
            partial(replacement.reserve_upload, changed, verified_caller_kid=_NEXT_KID),
            connection,
            (403, "artifact_caller_mismatch"),
        )
    finally:
        connection.close()


@pytest.mark.parametrize("operation", ["renew", "finalize", "orphan"])
def test_reconfigured_caller_precedes_stale_attempt_fence(
    tmp_path: Path,
    operation: str,
) -> None:
    connection = open_v6_database(tmp_path / f"caller-stale-{operation}.sqlite3")
    try:
        seed_accepted_task(connection, task_id=TASK_ID, manifest_id=_MANIFEST_ID)
        clock = MutableClock()
        original_writer = _writer(connection, clock, BOARD_KID)
        request = _request()
        reservation = original_writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        stale = attempt_fence(reservation)
        stale["attempt_nonce"] = "f" * 32
        replacement = _writer(connection, clock, _NEXT_KID)
        if operation == "renew":
            call = partial(
                replacement.renew_upload,
                request.upload_operation_id,
                **stale,
                verified_caller_kid=_NEXT_KID,
            )
        elif operation == "finalize":
            call = partial(
                replacement.finalize_upload,
                request.upload_operation_id,
                **stale,
                observed_size_bytes=request.declared_size_bytes,
                observed_sha256=request.content_sha256,
                verified_caller_kid=_NEXT_KID,
            )
        else:
            call = partial(
                replacement.orphan_upload,
                request.upload_operation_id,
                **stale,
                failure_code="client_disconnected",
                verified_caller_kid=_NEXT_KID,
            )
        _assert_error(call, connection, (403, "artifact_caller_mismatch"))
    finally:
        connection.close()

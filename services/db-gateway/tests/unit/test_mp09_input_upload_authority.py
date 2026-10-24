"""Additive MP-09 caller ownership and strict scalar boundary proofs."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import parse_artifact_policy
from db_gateway_service.services.artifact_writer import ArtifactWriter
from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    NOW_US,
    POSTER_ID,
    PUBLICATION_ID,
    TASK_ID,
    admission_payload,
    full_snapshot,
    open_prototype_database,
    require_prepublication_api,
    seed_poster,
)
from tests.fakes.mp09_input_upload_fixture import (
    InputClock,
    input_upload_payload,
    parse_scoped_request,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.unit

_MANIFEST_ID = "manifest-input-upload-authority"
_ROTATED_BOARD_KID = "gateway-task-board-rotated"


def _open_owned_scope(tmp_path: Path) -> sqlite3.Connection:
    connection = open_prototype_database(tmp_path / "input-upload-authority.sqlite3")
    seed_poster(connection)
    api = require_prepublication_api()
    request = api.requests.parse_publication_admission_v2(admission_payload(expected_input_count=0))
    connection.execute(
        "INSERT INTO board_task_publications (operation_id,publication_contract_version,"
        "task_id,canonical_request_json,request_sha256,caller_kid,actor_id,lock_operation_id,"
        "state,admitted_at_us,request_protocol_version,expected_input_count,"
        "base_request_sha256) VALUES (?,?,?,?,?,?,?,?,'awaiting_inputs',?,2,0,?)",
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
    return connection


def _writer(connection: sqlite3.Connection, clock: Callable[[], int]) -> ArtifactWriter:
    policy = parse_artifact_policy(
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
    return ArtifactWriter(
        connection,
        clock_us=clock,
        policy=policy,
        current_task_board_caller_kid=_ROTATED_BOARD_KID,
    )


def _service_error(call: Callable[[], Any]) -> ServiceError:
    with pytest.raises(ServiceError) as raised:
        call()
    return raised.value


def test_input_reservation_rejects_current_caller_that_does_not_own_publication(
    tmp_path: Path,
) -> None:
    connection = _open_owned_scope(tmp_path)
    try:
        body = b"x" * 33
        payload = input_upload_payload(
            operation_number=1,
            manifest_id=_MANIFEST_ID,
            body=body,
            display_filename="oversized.txt",
        )
        assert payload["publication_operation_id"] == PUBLICATION_ID
        assert payload["task_id"] == TASK_ID
        assert payload["manifest_id"] == _MANIFEST_ID
        assert payload["actor_id"] == POSTER_ID
        assert payload["resource"] == f"POST /task-publications/{PUBLICATION_ID}/inputs"
        assert len(body) == 33
        publication = connection.execute(
            "SELECT caller_kid,expected_input_count FROM board_task_publications "
            "WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert publication is not None and tuple(publication) == (BOARD_KID, 0)
        assert _ROTATED_BOARD_KID != BOARD_KID
        request = parse_scoped_request(payload)
        before = full_snapshot(connection)
        error = _service_error(
            lambda: _writer(connection, InputClock()).reserve_upload(
                request,
                verified_caller_kid=_ROTATED_BOARD_KID,
            )
        )
        assert (error.status_code, error.error) == (403, "artifact_caller_mismatch")
        assert full_snapshot(connection) == before
        assert connection.execute("SELECT COUNT(*) FROM board_artifact_uploads").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM board_artifact_upload_attempts").fetchone()[0]
            == 0
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol_version", True),
        ("declared_size_bytes", True),
        ("upload_operation_id", "not-a-uuid"),
        ("upload_operation_id", "80000000-0000-1000-8000-000000000001"),
    ],
)
def test_scoped_input_parser_rejects_bools_and_invalid_raw_upload_ids(
    field: str,
    value: object,
) -> None:
    payload = input_upload_payload(
        operation_number=1,
        manifest_id=_MANIFEST_ID,
        body=b"input\n",
        display_filename="input.txt",
    )
    payload[field] = value
    error = _service_error(lambda: parse_scoped_request(payload))
    assert (error.status_code, error.error) == (400, "invalid_payload")

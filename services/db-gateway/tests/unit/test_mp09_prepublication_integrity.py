"""Behavioral reds for prepublication caller and persisted-state integrity."""

from __future__ import annotations

import sqlite3
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.artifact_upload_requests import parse_artifact_policy
from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    FREEZE_ID,
    NOW_US,
    POSTER_ID,
    PUBLICATION_ID,
    admission_payload,
    canonical_json,
    freeze_payload,
    full_snapshot,
    make_writer,
    open_prototype_database,
    publish_payload,
    require_prepublication_api,
    seed_poster,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = pytest.mark.unit

_NEXT_KID = "gateway-task-board-next"


def _open_writer(tmp_path: Path) -> tuple[Any, sqlite3.Connection, Any]:
    api = require_prepublication_api()
    connection = open_prototype_database(tmp_path / "prepublication-integrity.sqlite3")
    seed_poster(connection)
    return api, connection, make_writer(api, connection)


def _writer_for_kid(api: Any, connection: sqlite3.Connection, kid: str) -> Any:
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
    return api.writer_type(
        connection,
        clock_us=lambda: NOW_US,
        artifact_policy=policy,
        current_task_board_caller_kid=kid,
    )


def _admit(api: Any, writer: Any) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_publication_admission_v2(admission_payload(expected_input_count=0))
    return request, writer.admit(request, verified_caller_kid=BOARD_KID)


def _freeze(api: Any, writer: Any, manifest_id: str) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))
    return request, writer.freeze_inputs(request, verified_caller_kid=BOARD_KID)


def _publish(api: Any, writer: Any, contract_sha256: str) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_publication_publish_v2(publish_payload(contract_sha256))
    return request, writer.authorize_publish(request, verified_caller_kid=BOARD_KID)


def _manifest_id(admitted: dict[str, Any]) -> str:
    value = admitted["input_manifest_id"]
    assert isinstance(value, str) and value
    return value


def _assert_integrity_error(call: Callable[[], object], connection: sqlite3.Connection) -> None:
    before = full_snapshot(connection)
    with pytest.raises(ServiceError) as raised:
        call()
    assert (raised.value.status_code, raised.value.error) == (500, "publication_state_corrupt")
    assert full_snapshot(connection) == before


def test_valid_prepublication_integrity_control_reaches_awaiting_lock(tmp_path: Path) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _admission, admitted = _admit(api, writer)
        _freeze_request, frozen = _freeze(api, writer, _manifest_id(admitted))
        _publish_request, authorized = _publish(
            api,
            writer,
            str(frozen["publication_contract_sha256"]),
        )
        assert authorized["publication_state"] == "awaiting_lock"
        assert (
            writer.get(
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
            == authorized
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    "operation",
    [
        "admit_replay",
        "admit_changed",
        "freeze_new",
        "freeze_replay",
        "publish_new",
        "publish_replay",
        "get",
        "reconcile",
    ],
)
def test_reconfigured_writer_cannot_access_prior_caller_publication(
    tmp_path: Path,
    operation: str,
) -> None:
    api, connection, original = _open_writer(tmp_path)
    try:
        admission_request, admitted = _admit(api, original)
        manifest_id = _manifest_id(admitted)
        freeze_request = api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))
        publish_request: Any = None
        if operation in {"freeze_replay", "publish_new", "publish_replay"}:
            freeze_request, frozen = _freeze(api, original, manifest_id)
            publish_request = api.requests.parse_publication_publish_v2(
                publish_payload(str(frozen["publication_contract_sha256"]))
            )
        if operation == "publish_replay":
            publish_request, _authorized = _publish(
                api,
                original,
                str(frozen["publication_contract_sha256"]),
            )
        replacement = _writer_for_kid(api, connection, _NEXT_KID)
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            if operation == "admit_replay":
                replacement.admit(admission_request, verified_caller_kid=_NEXT_KID)
            elif operation == "admit_changed":
                changed = api.requests.parse_publication_admission_v2(
                    admission_payload(expected_input_count=0, title="Changed by next caller")
                )
                replacement.admit(changed, verified_caller_kid=_NEXT_KID)
            elif operation in {"freeze_new", "freeze_replay"}:
                replacement.freeze_inputs(freeze_request, verified_caller_kid=_NEXT_KID)
            elif operation in {"publish_new", "publish_replay"}:
                replacement.authorize_publish(publish_request, verified_caller_kid=_NEXT_KID)
            elif operation == "get":
                replacement.get(
                    PUBLICATION_ID,
                    verified_caller_kid=_NEXT_KID,
                    expected_actor_id=POSTER_ID,
                )
            else:
                replacement.reconcile(PUBLICATION_ID, verified_caller_kid=_NEXT_KID)
        expected = (
            (404, "publication_not_found")
            if operation == "get"
            else (403, "operation_caller_mismatch")
        )
        assert (raised.value.status_code, raised.value.error) == expected
        assert full_snapshot(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize("tamper", ["canonical", "matching_hashes"])
@pytest.mark.parametrize("operation", ["replay", "freeze", "publish", "get", "reconcile"])
def test_admission_tampering_is_detected_before_replay_or_state_logic(
    tmp_path: Path,
    tamper: str,
    operation: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        admission_request, admitted = _admit(api, writer)
        manifest_id = _manifest_id(admitted)
        freeze_request = api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))
        publish_request = api.requests.parse_publication_publish_v2(publish_payload("a" * 64))
        if tamper == "canonical":
            altered = admission_payload(expected_input_count=0, title="Tampered persisted title")
            connection.execute(
                "UPDATE board_task_publications SET canonical_request_json=? WHERE operation_id=?",
                (canonical_json(altered), PUBLICATION_ID),
            )
        else:
            connection.execute(
                "UPDATE board_task_publications SET request_sha256=?,base_request_sha256=? "
                "WHERE operation_id=?",
                ("f" * 64, "f" * 64, PUBLICATION_ID),
            )
        connection.commit()

        if operation == "replay":
            call = partial(writer.admit, admission_request, verified_caller_kid=BOARD_KID)
        elif operation == "freeze":
            call = partial(
                writer.freeze_inputs,
                freeze_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "publish":
            call = partial(
                writer.authorize_publish,
                publish_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "get":
            call = partial(
                writer.get,
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
        else:
            call = partial(writer.reconcile, PUBLICATION_ID, verified_caller_kid=BOARD_KID)
        _assert_integrity_error(call, connection)
    finally:
        connection.close()


@pytest.mark.parametrize("tamper", ["manifest_canonical", "manifest_hash", "contract_hash"])
@pytest.mark.parametrize("operation", ["freeze_replay", "publish", "get", "reconcile"])
def test_frozen_manifest_or_contract_tampering_is_detected_everywhere(
    tmp_path: Path,
    tamper: str,
    operation: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _admission, admitted = _admit(api, writer)
        manifest_id = _manifest_id(admitted)
        freeze_request, frozen = _freeze(api, writer, manifest_id)
        publish_request = api.requests.parse_publication_publish_v2(
            publish_payload(str(frozen["publication_contract_sha256"]))
        )
        if tamper == "manifest_canonical":
            connection.execute(
                "UPDATE board_artifact_manifests SET canonical_manifest_json='{}' "
                "WHERE manifest_id=?",
                (manifest_id,),
            )
        elif tamper == "manifest_hash":
            connection.execute(
                "UPDATE board_artifact_manifests SET manifest_sha256=? WHERE manifest_id=?",
                ("f" * 64, manifest_id),
            )
        else:
            connection.execute(
                "UPDATE board_task_publications SET publication_contract_sha256=? "
                "WHERE operation_id=?",
                ("f" * 64, PUBLICATION_ID),
            )
        connection.commit()

        if operation == "freeze_replay":
            call = partial(
                writer.freeze_inputs,
                freeze_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "publish":
            call = partial(
                writer.authorize_publish,
                publish_request,
                verified_caller_kid=BOARD_KID,
            )
        elif operation == "get":
            call = partial(
                writer.get,
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
        else:
            call = partial(writer.reconcile, PUBLICATION_ID, verified_caller_kid=BOARD_KID)
        _assert_integrity_error(call, connection)
    finally:
        connection.close()


@pytest.mark.parametrize("operation", ["freeze", "publish"])
def test_raise_ignore_journal_update_is_persistence_failure_with_full_rollback(
    tmp_path: Path,
    operation: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _admission, admitted = _admit(api, writer)
        manifest_id = _manifest_id(admitted)
        if operation == "freeze":
            request = api.requests.parse_input_manifest_freeze_v2(freeze_payload(manifest_id))
            connection.execute(
                "CREATE TRIGGER ignore_freeze_journal_update "
                "BEFORE UPDATE OF state ON board_task_publications "
                "WHEN NEW.state='awaiting_publish' BEGIN SELECT RAISE(IGNORE); END"
            )
        else:
            _freeze_request, frozen = _freeze(api, writer, manifest_id)
            request = api.requests.parse_publication_publish_v2(
                publish_payload(str(frozen["publication_contract_sha256"]))
            )
            connection.execute(
                "CREATE TRIGGER ignore_publish_journal_update "
                "BEFORE UPDATE OF state ON board_task_publications "
                "WHEN NEW.state='awaiting_lock' BEGIN SELECT RAISE(IGNORE); END"
            )
        connection.commit()
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            if operation == "freeze":
                writer.freeze_inputs(request, verified_caller_kid=BOARD_KID)
            else:
                writer.authorize_publish(request, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "publication_persistence_failed",
        )
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def _invalid_operation_id(kind: str, command: str) -> str:
    source = FREEZE_ID if command == "freeze" else "op-60000000-0000-4000-8000-000000000003"
    if kind == "hex":
        return source[:-1] + "z"
    return source[:11] + "_" + source[12:]


@pytest.mark.parametrize("command", ["freeze", "publish"])
@pytest.mark.parametrize("invalid_kind", ["hex", "punctuation"])
def test_schema_rejects_invalid_v2_freeze_and_publish_operation_ids(
    tmp_path: Path,
    command: str,
    invalid_kind: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _admission, admitted = _admit(api, writer)
        manifest_id = _manifest_id(admitted)
        invalid_id = _invalid_operation_id(invalid_kind, command)
        before = full_snapshot(connection)
        with pytest.raises(sqlite3.IntegrityError):
            if command == "freeze":
                connection.execute(
                    "UPDATE board_task_publications SET state='awaiting_publish',"
                    "freeze_operation_id=?,canonical_freeze_request_json='{}',"
                    "freeze_request_sha256=?,input_manifest_sha256=?,"
                    "publication_contract_sha256=?,inputs_frozen_at_us=? "
                    "WHERE operation_id=?",
                    (invalid_id, "a" * 64, "b" * 64, "c" * 64, NOW_US, PUBLICATION_ID),
                )
            else:
                _freeze(api, writer, manifest_id)
                before = full_snapshot(connection)
                connection.execute(
                    "UPDATE board_task_publications SET state='awaiting_lock',"
                    "publish_operation_id=?,canonical_publish_request_json='{}',"
                    "publish_request_sha256=?,publish_authorized_at_us=? "
                    "WHERE operation_id=?",
                    (invalid_id, "d" * 64, NOW_US, PUBLICATION_ID),
                )
        connection.rollback()
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_schema_requires_lowercase_v2_task_id_while_preserving_v1_case(tmp_path: Path) -> None:
    connection = open_prototype_database(tmp_path / "task-case.sqlite3")
    try:
        seed_poster(connection)
        connection.execute(
            "INSERT INTO board_task_publications "
            "(operation_id,publication_contract_version,task_id,canonical_request_json,"
            "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
            "request_protocol_version,expected_input_count,base_request_sha256) "
            "VALUES ('op-70000000-0000-4000-8000-000000000001',1,?, '{}',?, ?, ?,"
            "'op-70000000-0000-4000-8000-000000000002','awaiting_lock',?,1,NULL,NULL)",
            ("t-AAAAAAAA-0000-4000-8000-000000000001", "a" * 64, BOARD_KID, POSTER_ID, NOW_US),
        )
        connection.commit()
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_task_publications WHERE request_protocol_version=1"
            ).fetchone()[0]
            == 1
        )

        before = full_snapshot(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO board_task_publications "
                "(operation_id,publication_contract_version,task_id,canonical_request_json,"
                "request_sha256,caller_kid,actor_id,lock_operation_id,state,admitted_at_us,"
                "request_protocol_version,expected_input_count,base_request_sha256) "
                "VALUES ('op-70000000-0000-4000-8000-000000000003',1,?, '{}',?,?,?,"
                "'op-70000000-0000-4000-8000-000000000004','awaiting_inputs',?,2,0,?)",
                (
                    "t-BBBBBBBB-0000-4000-8000-000000000001",
                    "b" * 64,
                    BOARD_KID,
                    POSTER_ID,
                    NOW_US,
                    "b" * 64,
                ),
            )
        connection.rollback()
        assert full_snapshot(connection) == before
    finally:
        connection.close()

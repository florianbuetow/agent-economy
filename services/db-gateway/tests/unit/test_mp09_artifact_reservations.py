"""T-126 red proof for the bounded V6 reservation writer slice."""

from __future__ import annotations

import hashlib
import importlib.resources
import inspect
import json
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.fakes.mp09_v6_artifact_fixture import (
    ACCEPTANCE_CUTOFF_US,
    ACCEPTANCE_WINDOW_SECONDS,
    BIDDING_CUTOFF_TEXT,
    BIDDING_CUTOFF_US,
    BIDDING_DURATION_SECONDS,
    BOARD_KID,
    BODY_A,
    BODY_B,
    EXECUTION_CUTOFF_TEXT,
    EXECUTION_CUTOFF_US,
    HASH_A,
    HASH_B,
    LEGACY_BINDING,
    MAX_UPLOAD_DURATION_SECONDS,
    NATIVE_BINDING,
    NOW_US,
    PUBLISHED_AT_TEXT,
    PUBLISHED_AT_US,
    TASK_ID,
    MutableClock,
    attempt_fence,
    database_snapshot,
    explicit_policy,
    make_writer,
    open_v6_database,
    require_v6_api,
    seed_accepted_task,
    upload_payload,
)

pytestmark = pytest.mark.unit

if TYPE_CHECKING:
    from pathlib import Path

_TASK_ID = TASK_ID
_MANIFEST_ID = "manifest-v6-reservation"


def _operation(number: int) -> str:
    return f"00000000-0000-4000-8000-{number:012d}"


def _request(api: Any, number: int, **overrides: object) -> Any:
    payload = upload_payload(
        operation_id=_operation(number),
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
    )
    payload.update(overrides)
    return api.requests.parse_artifact_upload_request(payload)


def _open_seeded(
    tmp_path: Path,
    *,
    binding_kind: str = "publication_contract",
    binding_sha256: str = NATIVE_BINDING,
) -> tuple[Any, sqlite3.Connection, MutableClock]:
    api = require_v6_api()
    connection = open_v6_database(tmp_path / "artifact-v6.sqlite3")
    seed_accepted_task(
        connection,
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
        binding_kind=binding_kind,
        binding_sha256=binding_sha256,
    )
    return api, connection, MutableClock()


def _error(call: Any) -> ServiceError:
    with pytest.raises(ServiceError) as raised:
        call()
    return raised.value


def _reserve(writer: Any, request: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", writer.reserve_upload(request, verified_caller_kid=BOARD_KID))


def _finalize(writer: Any, request: Any, reservation: dict[str, Any]) -> dict[str, Any]:
    return cast(
        "dict[str, Any]",
        writer.finalize_upload(
            request.upload_operation_id,
            **attempt_fence(reservation),
            observed_size_bytes=request.declared_size_bytes,
            observed_sha256=request.content_sha256,
            verified_caller_kid=BOARD_KID,
        ),
    )


def test_fixture_execution_cutoff_outlives_maximum_upload_clock() -> None:
    assert parse_utc_text_to_epoch_us(EXECUTION_CUTOFF_TEXT) == EXECUTION_CUTOFF_US
    assert EXECUTION_CUTOFF_US > NOW_US + MAX_UPLOAD_DURATION_SECONDS * 1_000_000


def test_fixture_publication_deadlines_follow_signed_durations() -> None:
    assert parse_utc_text_to_epoch_us(PUBLISHED_AT_TEXT) == PUBLISHED_AT_US
    assert parse_utc_text_to_epoch_us(BIDDING_CUTOFF_TEXT) == BIDDING_CUTOFF_US
    assert BIDDING_CUTOFF_US == PUBLISHED_AT_US + BIDDING_DURATION_SECONDS * 1_000_000
    assert ACCEPTANCE_CUTOFF_US == BIDDING_CUTOFF_US + ACCEPTANCE_WINDOW_SECONDS * 1_000_000


def _assert_uuid4(value: object) -> None:
    parsed = UUID(str(value))
    assert parsed.version == 4
    assert str(parsed) == value


def _assert_persisted_reservation(
    connection: sqlite3.Connection,
    request: Any,
    reservation: dict[str, Any],
    clock: MutableClock,
) -> None:
    attempt = reservation["attempt"]
    _assert_uuid4(reservation["artifact_id"])
    _assert_uuid4(attempt["attempt_id"])
    assert isinstance(attempt["owner_id"], str) and attempt["owner_id"]
    assert re.fullmatch(r"[0-9a-f]{32}", str(attempt["attempt_nonce"]))
    artifact_id = reservation["artifact_id"]
    assert reservation["object_key"] == f"live/{str(artifact_id)[:2]}/{artifact_id}.blob"
    assert attempt["staging_key"] == (
        f"staging/{request.upload_operation_id}.1.{attempt['attempt_nonce']}.part"
    )
    assert attempt["generation"] == 1
    assert attempt["state"] == "active"
    assert attempt["lease_expires_at_us"] == clock.now_us + 300_000_000
    assert attempt["absolute_upload_deadline_us"] == clock.now_us + 900_000_000

    upload = connection.execute(
        "SELECT upload_operation_id, request_sha256, resource, task_id, manifest_id, "
        "actor_id, actor_role, display_filename, evidence_format, media_type, "
        "declared_size_bytes, content_sha256, expected_lifecycle_version, "
        "accepted_binding_kind, accepted_binding_sha256, state, artifact_id, object_key, "
        "current_attempt_id, created_at_us, finalized_at_us "
        "FROM board_artifact_uploads WHERE upload_operation_id=?",
        (request.upload_operation_id,),
    ).fetchone()
    assert upload is not None
    assert tuple(upload) == (
        request.upload_operation_id,
        request.request_sha256,
        request.resource,
        request.task_id,
        request.manifest_id,
        request.actor_id,
        request.actor_role,
        request.display_filename,
        request.evidence_format,
        "text/plain; charset=utf-8",
        request.declared_size_bytes,
        request.content_sha256,
        request.expected_lifecycle_version,
        request.accepted_binding_kind,
        request.accepted_binding_sha256,
        "reserved",
        artifact_id,
        reservation["object_key"],
        attempt["attempt_id"],
        clock.now_us,
        None,
    )
    persisted_attempt = connection.execute(
        "SELECT attempt_id, upload_operation_id, owner_id, generation, attempt_nonce, "
        "staging_key, intended_object_key, lease_expires_at_us, "
        "absolute_upload_deadline_us, state, observed_size_bytes, observed_sha256, "
        "failure_code, created_at_us, updated_at_us "
        "FROM board_artifact_upload_attempts WHERE attempt_id=?",
        (attempt["attempt_id"],),
    ).fetchone()
    assert persisted_attempt is not None
    assert tuple(persisted_attempt) == (
        attempt["attempt_id"],
        request.upload_operation_id,
        attempt["owner_id"],
        1,
        attempt["attempt_nonce"],
        attempt["staging_key"],
        reservation["object_key"],
        attempt["lease_expires_at_us"],
        attempt["absolute_upload_deadline_us"],
        "active",
        None,
        None,
        None,
        clock.now_us,
        clock.now_us,
    )


def test_v6_prototype_preserves_base_resources_and_publication_sidecars(tmp_path: Path) -> None:
    resources = importlib.resources.files("db_gateway_service.schema_resources")
    baseline = sqlite3.connect(":memory:")
    for resource_name in (
        "current_v6.sql",
        "task_operations_v3.sql",
        "task_effect_recovery_v5.sql",
    ):
        baseline.executescript(resources.joinpath(resource_name).read_text())
    expected_rows = baseline.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    expected = {(str(row[0]), str(row[1])): tuple(row) for row in expected_rows}
    baseline.close()

    v4_reference = sqlite3.connect(":memory:")
    for resource_name in (
        "current_v6.sql",
        "task_operations_v3.sql",
        "publication_v4.sql",
        "task_effect_recovery_v5.sql",
    ):
        v4_reference.executescript(resources.joinpath(resource_name).read_text())
    v4_rows = v4_reference.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    v4_catalog = {(str(row[0]), str(row[1])): tuple(row) for row in v4_rows}
    v4_reference.close()
    publication_sidecars = {
        key: row
        for key, row in v4_catalog.items()
        if key not in expected and key != ("table", "board_task_publications")
    }

    connection = open_v6_database(tmp_path / "artifact-v6.sqlite3")
    try:
        actual_rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        actual = {(str(row[0]), str(row[1])): tuple(row) for row in actual_rows}
        assert {key: actual.get(key) for key in expected} == expected
        assert {key: actual.get(key) for key in publication_sidecars} == publication_sidecars
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {
            "board_artifact_manifests",
            "board_artifact_uploads",
            "board_artifact_upload_attempts",
            "board_artifact_archive_attempts",
            "board_task_artifact_contracts",
            "board_legacy_artifact_observations",
            "board_artifact_cutover",
        } <= tables
        metadata = connection.execute(
            "SELECT version FROM schema_metadata WHERE singleton=1"
        ).fetchone()
        assert metadata is not None
        assert tuple(metadata) == (6,)
    finally:
        connection.close()


def test_artifact_policy_is_explicit_strict_and_has_no_implicit_values() -> None:
    api = require_v6_api()
    signature = inspect.signature(api.writer_type)
    policy_parameter = signature.parameters.get("policy")
    assert policy_parameter is not None
    assert policy_parameter.default is inspect.Parameter.empty

    valid: dict[str, object] = {
        "contract_version": 1,
        "max_file_bytes": 32,
        "max_files_per_manifest": 2,
        "max_manifest_bytes": 48,
        "reservation_ttl_seconds": 300,
        "reservation_renew_before_expiry_seconds": 60,
        "max_upload_duration_seconds": 900,
    }
    for missing in valid:
        incomplete = dict(valid)
        del incomplete[missing]
        with pytest.raises(ServiceError) as raised:
            api.requests.parse_artifact_policy(incomplete)
        assert raised.value.error == "invalid_artifact_policy"

    for field, invalid in (
        ("max_file_bytes", True),
        ("max_files_per_manifest", 0),
        ("max_manifest_bytes", 1 << 63),
        ("reservation_ttl_seconds", "300"),
        ("reservation_renew_before_expiry_seconds", -1),
        ("max_upload_duration_seconds", 0),
    ):
        malformed = dict(valid)
        malformed[field] = invalid
        with pytest.raises(ServiceError) as raised:
            api.requests.parse_artifact_policy(malformed)
        assert raised.value.error == "invalid_artifact_policy"

    extra = dict(valid)
    extra["production_default"] = 1
    with pytest.raises(ServiceError) as raised:
        api.requests.parse_artifact_policy(extra)
    assert raised.value.error == "invalid_artifact_policy"

    for inconsistent in (
        {**valid, "max_file_bytes": 49},
        {**valid, "reservation_renew_before_expiry_seconds": 300},
        {**valid, "reservation_ttl_seconds": 901},
    ):
        with pytest.raises(ServiceError) as raised:
            api.requests.parse_artifact_policy(inconsistent)
        assert raised.value.error == "invalid_artifact_policy"


def test_upload_request_parser_is_strict_canonical_and_finite() -> None:
    api = require_v6_api()
    valid = upload_payload(
        operation_id=_operation(1),
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
    )
    for missing in valid:
        incomplete = dict(valid)
        del incomplete[missing]
        with pytest.raises(ServiceError) as raised:
            api.requests.parse_artifact_upload_request(incomplete)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")

    extra = dict(valid)
    extra["client_default"] = 1
    with pytest.raises(ServiceError) as raised:
        api.requests.parse_artifact_upload_request(extra)
    assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")

    invalid_fields: tuple[tuple[str, object], ...] = (
        ("protocol_version", True),
        ("protocol_version", 2),
        ("upload_operation_id", f"op-{_operation(1)}"),
        ("upload_operation_id", "00000000-0000-4000-A000-000000000001"),
        ("resource", "POST /board/artifact-uploads/other"),
        ("task_id", ""),
        ("manifest_id", ""),
        ("actor_id", ""),
        ("actor_role", "administrator"),
        ("display_filename", ""),
        ("evidence_format", "binary"),
        ("declared_size_bytes", True),
        ("declared_size_bytes", -1),
        ("declared_size_bytes", 1 << 63),
        ("content_sha256", HASH_A.upper()),
        ("content_sha256", "f" * 63),
        ("expected_lifecycle_version", True),
        ("expected_lifecycle_version", -1),
        ("accepted_binding_kind", "publication"),
        ("accepted_binding_sha256", NATIVE_BINDING.upper()),
    )
    for field, value in invalid_fields:
        malformed = dict(valid)
        malformed[field] = value
        with pytest.raises(ServiceError) as raised:
            api.requests.parse_artifact_upload_request(malformed)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload"), field


@pytest.mark.parametrize(
    ("binding_kind", "binding_sha256"),
    [
        ("publication_contract", NATIVE_BINDING),
        ("legacy_accepted_continuation", LEGACY_BINDING),
    ],
)
def test_native_and_legacy_bindings_are_discriminated_at_reservation(
    tmp_path: Path,
    binding_kind: str,
    binding_sha256: str,
) -> None:
    api, connection, clock = _open_seeded(
        tmp_path,
        binding_kind=binding_kind,
        binding_sha256=binding_sha256,
    )
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(
            api,
            1,
            accepted_binding_kind=binding_kind,
            accepted_binding_sha256=binding_sha256,
        )
        reservation = _reserve(writer, request)
        assert reservation["state"] == "reserved"
        assert reservation["accepted_binding_kind"] == binding_kind
        assert reservation["accepted_binding_sha256"] == binding_sha256

        changed = _request(
            api,
            2,
            accepted_binding_kind=(
                "legacy_accepted_continuation"
                if binding_kind == "publication_contract"
                else "publication_contract"
            ),
            accepted_binding_sha256=binding_sha256,
        )
        before = database_snapshot(connection)
        error = _error(lambda: _reserve(writer, changed))
        assert (error.status_code, error.error) == (409, "accepted_binding_mismatch")
        assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_correct_binding_kind_with_wrong_hash_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        before = database_snapshot(connection)
        request = _request(api, 1, accepted_binding_sha256="f" * 64)
        error = _error(lambda: _reserve(writer, request))
        assert (error.status_code, error.error) == (409, "accepted_binding_mismatch")
        assert database_snapshot(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize("client_field", ["owner_id", "generation", "attempt_nonce"])
def test_client_intent_rejects_server_issued_fence_fields(client_field: str) -> None:
    api = require_v6_api()
    payload = upload_payload(
        operation_id=_operation(1),
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
    )
    payload[client_field] = "client-selected"
    with pytest.raises(ServiceError) as raised:
        api.requests.parse_artifact_upload_request(payload)
    assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")


def test_lifecycle_version_conflict_precedes_status_conflict_without_mutation(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        connection.execute("UPDATE board_tasks SET status='submitted' WHERE task_id=?", (_TASK_ID,))
        connection.execute(
            "UPDATE board_task_contracts SET lifecycle_version=2 WHERE task_id=?", (_TASK_ID,)
        )
        connection.commit()
        writer = make_writer(api, connection, clock, explicit_policy(api))
        before = database_snapshot(connection)
        error = _error(lambda: _reserve(writer, _request(api, 1, expected_lifecycle_version=1)))
        assert (error.status_code, error.error) == (409, "task_version_conflict")
        assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_exact_active_replay_is_upload_pending_and_consumes_no_second_slot(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        reservation = _reserve(writer, request)
        _assert_persisted_reservation(connection, request, reservation, clock)
        before = database_snapshot(connection)
        error = _error(lambda: _reserve(writer, request))
        assert (error.status_code, error.error) == (409, "upload_pending")
        assert database_snapshot(connection) == before
        upload_count = connection.execute(
            "SELECT COUNT(*) FROM board_artifact_uploads WHERE manifest_id=?",
            (_MANIFEST_ID,),
        ).fetchone()
        assert upload_count is not None
        assert tuple(upload_count) == (1,)
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM board_artifact_upload_attempts WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        assert attempt_count is not None
        assert tuple(attempt_count) == (1,)
    finally:
        connection.close()


def test_exercised_v6_rows_enforce_primary_unique_foreign_key_and_state_checks(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        reservation = _reserve(writer, request)
        _assert_persisted_reservation(connection, request, reservation, clock)

        upload_columns = {
            str(row[1]): int(row[5])
            for row in connection.execute("PRAGMA table_info(board_artifact_uploads)")
        }
        attempt_columns = {
            str(row[1]): int(row[5])
            for row in connection.execute("PRAGMA table_info(board_artifact_upload_attempts)")
        }
        assert upload_columns["upload_operation_id"] == 1
        assert attempt_columns["attempt_id"] == 1
        unique_attempt_indexes = set()
        for index_row in connection.execute("PRAGMA index_list(board_artifact_upload_attempts)"):
            if int(index_row[2]) == 1:
                columns = tuple(
                    str(row[2]) for row in connection.execute(f"PRAGMA index_info({index_row[1]})")
                )
                unique_attempt_indexes.add(columns)
        assert ("upload_operation_id", "generation") in unique_attempt_indexes

        upload_foreign_keys = {
            (str(row[3]), str(row[2]), str(row[4]))
            for row in connection.execute("PRAGMA foreign_key_list(board_artifact_uploads)")
        }
        attempt_foreign_keys = {
            (str(row[3]), str(row[2]), str(row[4]))
            for row in connection.execute("PRAGMA foreign_key_list(board_artifact_upload_attempts)")
        }
        assert {
            ("manifest_id", "board_artifact_manifests", "manifest_id"),
            ("manifest_kind", "board_artifact_manifests", "kind"),
            ("task_id", "board_artifact_manifests", "task_id"),
            ("actor_id", "board_artifact_manifests", "actor_id"),
            (
                "publication_operation_id",
                "board_artifact_manifests",
                "publication_operation_id",
            ),
            ("current_attempt_id", "board_artifact_upload_attempts", "attempt_id"),
        } <= upload_foreign_keys
        assert {
            ("upload_operation_id", "board_artifact_uploads", "upload_operation_id")
        } <= attempt_foreign_keys

        for statement, parameters in (
            (
                "UPDATE board_artifact_uploads SET state='invalid' WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ),
            (
                "UPDATE board_artifact_uploads SET current_attempt_id='missing-attempt' "
                "WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ),
            (
                "UPDATE board_artifact_upload_attempts SET generation=0 WHERE attempt_id=?",
                (reservation["attempt"]["attempt_id"],),
            ),
        ):
            before = database_snapshot(connection)
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(statement, parameters)
            connection.rollback()
            assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_changed_upload_operation_conflicts_without_any_write(tmp_path: Path) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(
            api,
            connection,
            clock,
            explicit_policy(api, max_files_per_manifest=1),
        )
        original = _request(api, 1)
        _reserve(writer, original)
        connection.execute("UPDATE board_tasks SET status='submitted' WHERE task_id=?", (_TASK_ID,))
        connection.commit()
        before = database_snapshot(connection)
        changed = _request(api, 1, display_filename="changed.txt")
        error = _error(lambda: _reserve(writer, changed))
        assert (error.status_code, error.error) == (409, "upload_operation_conflict")
        assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_exact_finalized_replay_returns_original_artifact_without_new_attempt(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(
            api,
            connection,
            clock,
            explicit_policy(api, max_files_per_manifest=1),
        )
        request = _request(api, 1)
        reserved = _reserve(writer, request)
        for observed_size, observed_hash in (
            (len(BODY_A) + 1, HASH_A),
            (len(BODY_A), "f" * 64),
        ):
            before = database_snapshot(connection)
            error = _error(
                lambda observed_size=observed_size, observed_hash=observed_hash: (
                    writer.finalize_upload(
                        request.upload_operation_id,
                        **attempt_fence(reserved),
                        observed_size_bytes=observed_size,
                        observed_sha256=observed_hash,
                        verified_caller_kid=BOARD_KID,
                    )
                )
            )
            assert (error.status_code, error.error) == (409, "upload_content_mismatch")
            assert database_snapshot(connection) == before

        finalized = _finalize(writer, request, reserved)
        upload = connection.execute(
            "SELECT state, current_attempt_id, artifact_id, object_key, finalized_at_us "
            "FROM board_artifact_uploads WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        assert upload is not None
        assert tuple(upload) == (
            "ready",
            None,
            reserved["artifact_id"],
            reserved["object_key"],
            clock.now_us,
        )
        attempt = connection.execute(
            "SELECT state, observed_size_bytes, observed_sha256, failure_code, updated_at_us "
            "FROM board_artifact_upload_attempts WHERE attempt_id=?",
            (reserved["attempt"]["attempt_id"],),
        ).fetchone()
        assert attempt is not None
        assert tuple(attempt) == ("finalized", len(BODY_A), HASH_A, None, clock.now_us)

        connection.execute("UPDATE board_tasks SET status='submitted' WHERE task_id=?", (_TASK_ID,))
        connection.execute(
            "UPDATE board_task_contracts SET lifecycle_version=2 WHERE task_id=?", (_TASK_ID,)
        )
        connection.commit()
        before = database_snapshot(connection)
        replay = _reserve(writer, request)
        assert finalized["state"] == "ready"
        assert replay["state"] == "ready"
        assert replay["artifact_id"] == reserved["artifact_id"]
        assert replay["object_key"] == reserved["object_key"]
        assert replay["artifact_id"] == finalized["artifact_id"]
        assert replay["object_key"] == finalized["object_key"]
        assert replay["content_sha256"] == HASH_A
        assert replay["declared_size_bytes"] == len(BODY_A)
        assert database_snapshot(connection) == before
        attempt_count = connection.execute(
            "SELECT COUNT(*) FROM board_artifact_upload_attempts WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        assert attempt_count is not None
        assert tuple(attempt_count) == (1,)
    finally:
        connection.close()


def test_authorized_orphan_records_exact_attempt_and_clears_current_owner(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        reserved = _reserve(writer, request)
        orphaned = writer.orphan_upload(
            request.upload_operation_id,
            **attempt_fence(reserved),
            failure_code="client_disconnected",
            verified_caller_kid=BOARD_KID,
        )
        assert orphaned["state"] == "orphaned"
        assert orphaned["artifact_id"] == reserved["artifact_id"]
        assert orphaned["object_key"] == reserved["object_key"]
        upload = connection.execute(
            "SELECT state, current_attempt_id, artifact_id, object_key, finalized_at_us "
            "FROM board_artifact_uploads WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        assert upload is not None
        assert tuple(upload) == (
            "orphaned",
            None,
            reserved["artifact_id"],
            reserved["object_key"],
            clock.now_us,
        )
        attempt = connection.execute(
            "SELECT state, observed_size_bytes, observed_sha256, failure_code, updated_at_us "
            "FROM board_artifact_upload_attempts WHERE attempt_id=?",
            (reserved["attempt"]["attempt_id"],),
        ).fetchone()
        assert attempt is not None
        assert tuple(attempt) == (
            "orphaned",
            None,
            None,
            "client_disconnected",
            clock.now_us,
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_finalize_after_lifecycle_change_durably_orphans_owned_attempt(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        reserved = _reserve(writer, request)
        connection.execute("UPDATE board_tasks SET status='submitted' WHERE task_id=?", (_TASK_ID,))
        connection.execute(
            "UPDATE board_task_contracts SET lifecycle_version=2 WHERE task_id=?", (_TASK_ID,)
        )
        connection.commit()

        error = _error(lambda: _finalize(writer, request, reserved))
        assert (error.status_code, error.error) == (409, "task_version_conflict")
        upload = connection.execute(
            "SELECT state, current_attempt_id, finalized_at_us FROM board_artifact_uploads "
            "WHERE upload_operation_id=?",
            (request.upload_operation_id,),
        ).fetchone()
        assert upload is not None
        assert tuple(upload) == ("orphaned", None, clock.now_us)
        attempt = connection.execute(
            "SELECT state, observed_size_bytes, observed_sha256, failure_code, updated_at_us "
            "FROM board_artifact_upload_attempts WHERE attempt_id=?",
            (reserved["attempt"]["attempt_id"],),
        ).fetchone()
        assert attempt is not None
        assert tuple(attempt) == (
            "orphaned",
            None,
            None,
            "task_version_conflict",
            clock.now_us,
        )
        task = connection.execute(
            "SELECT status, lifecycle_version FROM board_tasks "
            "JOIN board_task_contracts USING (task_id) WHERE board_tasks.task_id=?",
            (_TASK_ID,),
        ).fetchone()
        assert task is not None
        assert tuple(task) == ("submitted", 2)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_finalize_after_current_lease_expiry_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        reserved = _reserve(writer, request)
        clock.now_us = int(reserved["attempt"]["lease_expires_at_us"]) + 1
        before = database_snapshot(connection)
        error = _error(lambda: _finalize(writer, request, reserved))
        assert (error.status_code, error.error) == (409, "upload_lease_expired")
        assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_atomic_file_quota_allows_only_one_of_two_gateway_connections(tmp_path: Path) -> None:
    api = require_v6_api()
    database_path = tmp_path / "artifact-v6.sqlite3"
    seed_connection = open_v6_database(database_path)
    seed_accepted_task(
        seed_connection,
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
    )
    seed_connection.close()
    clock = MutableClock()
    barrier = Barrier(2)

    def reserve(number: int) -> tuple[str, str]:
        connection = sqlite3.connect(database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            writer = make_writer(
                api,
                connection,
                clock,
                explicit_policy(api, max_files_per_manifest=1),
            )
            request = _request(api, number)
            barrier.wait(timeout=5)
            try:
                result = _reserve(writer, request)
            except ServiceError as exc:
                return "error", exc.error
            return "reserved", str(result["artifact_id"])
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(reserve, 1), pool.submit(reserve, 2))
        outcomes = sorted(future.result() for future in futures)

    assert [outcome[0] for outcome in outcomes] == ["error", "reserved"]
    assert outcomes[0][1] == "too_many_assets"
    verification = sqlite3.connect(database_path)
    try:
        upload_count = verification.execute(
            "SELECT COUNT(*) FROM board_artifact_uploads WHERE manifest_id=? "
            "AND state IN ('reserved', 'ready')",
            (_MANIFEST_ID,),
        ).fetchone()
        assert upload_count is not None
        assert tuple(upload_count) == (1,)
        attempt_count = verification.execute(
            "SELECT COUNT(*) FROM board_artifact_upload_attempts"
        ).fetchone()
        assert attempt_count is not None
        assert tuple(attempt_count) == (1,)
    finally:
        verification.close()


def test_atomic_byte_quota_counts_ready_and_concurrent_reserved_rows(tmp_path: Path) -> None:
    api = require_v6_api()
    database_path = tmp_path / "artifact-v6.sqlite3"
    seed_connection = open_v6_database(database_path)
    seed_accepted_task(
        seed_connection,
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
    )
    clock = MutableClock()
    policy = explicit_policy(api, max_files_per_manifest=3, max_manifest_bytes=32)
    seed_writer = make_writer(api, seed_connection, clock, policy)
    ready_request = _request(api, 1)
    _finalize(seed_writer, ready_request, _reserve(seed_writer, ready_request))
    seed_connection.close()
    barrier = Barrier(2)

    def reserve(number: int) -> tuple[str, str]:
        connection = sqlite3.connect(database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            writer = make_writer(api, connection, clock, policy)
            request = _request(
                api,
                number,
                declared_size_bytes=10,
                content_sha256=f"{number}" * 64,
                display_filename=f"candidate-{number}.txt",
            )
            barrier.wait(timeout=5)
            try:
                result = _reserve(writer, request)
            except ServiceError as exc:
                return "error", exc.error
            return "reserved", str(result["artifact_id"])
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (pool.submit(reserve, 2), pool.submit(reserve, 3))
        outcomes = sorted(future.result() for future in futures)

    assert [outcome[0] for outcome in outcomes] == ["error", "reserved"]
    assert outcomes[0][1] == "manifest_bytes_exceeded"
    verification = sqlite3.connect(database_path)
    try:
        rows = verification.execute(
            "SELECT state, declared_size_bytes FROM board_artifact_uploads "
            "WHERE manifest_id=? ORDER BY upload_operation_id",
            (_MANIFEST_ID,),
        ).fetchall()
        assert len(rows) == 2
        assert sorted(tuple(row) for row in rows) == [
            ("ready", len(BODY_A)),
            ("reserved", 10),
        ]
        assert sum(int(row[1]) for row in rows) == len(BODY_A) + 10
        assert verification.execute(
            "SELECT COUNT(*) FROM board_artifact_upload_attempts"
        ).fetchone() == (2,)
    finally:
        verification.close()


def test_declared_byte_quota_counts_ready_and_reserved_rows(tmp_path: Path) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(
            api,
            connection,
            clock,
            explicit_policy(api, max_files_per_manifest=3, max_manifest_bytes=32),
        )
        first = _request(api, 1)
        _finalize(writer, first, _reserve(writer, first))
        second = _request(
            api,
            2,
            declared_size_bytes=len(BODY_B),
            content_sha256=HASH_B,
            display_filename="second.txt",
        )
        _reserve(writer, second)
        before = database_snapshot(connection)
        third = _request(
            api,
            3,
            declared_size_bytes=4,
            content_sha256="3" * 64,
            display_filename="third.txt",
        )
        error = _error(lambda: _reserve(writer, third))
        assert (error.status_code, error.error) == (409, "manifest_bytes_exceeded")
        assert database_snapshot(connection) == before
        rows = connection.execute(
            "SELECT state, declared_size_bytes FROM board_artifact_uploads "
            "WHERE manifest_id=? ORDER BY upload_operation_id",
            (_MANIFEST_ID,),
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("ready", len(BODY_A)),
            ("reserved", len(BODY_B)),
        ]
    finally:
        connection.close()


def test_takeover_keeps_absolute_deadline_and_rejects_every_stale_fence(
    tmp_path: Path,
) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        first = _reserve(writer, request)
        first_attempt = first["attempt"]
        clock.now_us = int(first_attempt["lease_expires_at_us"]) + 1
        second = _reserve(writer, request)
        second_attempt = second["attempt"]
        assert second_attempt["generation"] == first_attempt["generation"] + 1
        assert second_attempt["owner_id"] != first_attempt["owner_id"]
        assert second_attempt["attempt_nonce"] != first_attempt["attempt_nonce"]
        assert second_attempt["staging_key"] != first_attempt["staging_key"]
        assert (
            second_attempt["absolute_upload_deadline_us"]
            == first_attempt["absolute_upload_deadline_us"]
        )

        for method_name, extra in (
            ("renew_upload", {}),
            ("orphan_upload", {"failure_code": "client_disconnected"}),
            (
                "finalize_upload",
                {"observed_size_bytes": len(BODY_A), "observed_sha256": HASH_A},
            ),
        ):
            before = database_snapshot(connection)
            method = getattr(writer, method_name)
            error = _error(
                lambda method=method, extra=extra: method(
                    request.upload_operation_id,
                    **attempt_fence(first),
                    **extra,
                    verified_caller_kid=BOARD_KID,
                )
            )
            assert (error.status_code, error.error) == (409, "stale_upload_attempt")
            assert database_snapshot(connection) == before

        attempts = connection.execute(
            "SELECT generation, state, absolute_upload_deadline_us "
            "FROM board_artifact_upload_attempts WHERE upload_operation_id=? "
            "ORDER BY generation",
            (request.upload_operation_id,),
        ).fetchall()
        assert [tuple(row) for row in attempts] == [
            (first_attempt["generation"], "expired", first_attempt["absolute_upload_deadline_us"]),
            (second_attempt["generation"], "active", first_attempt["absolute_upload_deadline_us"]),
        ]
    finally:
        connection.close()


def test_renewal_is_capped_and_takeover_fails_after_absolute_deadline(tmp_path: Path) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        reservation = _reserve(writer, request)
        absolute_deadline = int(reservation["attempt"]["absolute_upload_deadline_us"])
        lease_expires_at = int(reservation["attempt"]["lease_expires_at_us"])
        while lease_expires_at < absolute_deadline:
            clock.now_us = lease_expires_at - 60_000_000
            renewed = writer.renew_upload(
                request.upload_operation_id,
                **attempt_fence(reservation),
                verified_caller_kid=BOARD_KID,
            )
            renewed_expiry = int(renewed["lease_expires_at_us"])
            assert lease_expires_at < renewed_expiry <= absolute_deadline
            lease_expires_at = renewed_expiry
        assert lease_expires_at == absolute_deadline

        clock.now_us = absolute_deadline + 1
        before = database_snapshot(connection)
        error = _error(lambda: _reserve(writer, request))
        assert (error.status_code, error.error) == (409, "upload_deadline_exceeded")
        assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_renewal_after_lease_expiry_is_rejected_without_mutation(tmp_path: Path) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        reservation = _reserve(writer, request)
        clock.now_us = int(reservation["attempt"]["lease_expires_at_us"]) + 1
        before = database_snapshot(connection)
        error = _error(
            lambda: writer.renew_upload(
                request.upload_operation_id,
                **attempt_fence(reservation),
                verified_caller_kid=BOARD_KID,
            )
        )
        assert (error.status_code, error.error) == (409, "upload_lease_expired")
        assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_wrong_gateway_caller_cannot_reserve_or_mutate_attempt(tmp_path: Path) -> None:
    api, connection, clock = _open_seeded(tmp_path)
    try:
        writer = make_writer(api, connection, clock, explicit_policy(api))
        request = _request(api, 1)
        before = database_snapshot(connection)
        error = _error(lambda: writer.reserve_upload(request, verified_caller_kid="gateway-court"))
        assert (error.status_code, error.error) == (403, "artifact_caller_mismatch")
        assert database_snapshot(connection) == before

        reservation = _reserve(writer, request)
        before = database_snapshot(connection)
        error = _error(
            lambda: writer.renew_upload(
                request.upload_operation_id,
                **attempt_fence(reservation),
                verified_caller_kid="gateway-court",
            )
        )
        assert (error.status_code, error.error) == (403, "artifact_caller_mismatch")
        assert database_snapshot(connection) == before

        for method_name, extra in (
            (
                "finalize_upload",
                {"observed_size_bytes": len(BODY_A), "observed_sha256": HASH_A},
            ),
            ("orphan_upload", {"failure_code": "client_disconnected"}),
        ):
            before = database_snapshot(connection)
            method = getattr(writer, method_name)
            error = _error(
                lambda method=method, extra=extra: method(
                    request.upload_operation_id,
                    **attempt_fence(reservation),
                    **extra,
                    verified_caller_kid="gateway-court",
                )
            )
            assert (error.status_code, error.error) == (403, "artifact_caller_mismatch")
            assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_request_hash_binds_every_immutable_intent_field() -> None:
    api = require_v6_api()
    original_payload = upload_payload(
        operation_id=_operation(1),
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
    )
    original = api.requests.parse_artifact_upload_request(original_payload)
    expected_json = json.dumps(
        original_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert original.canonical_json == expected_json
    assert original.request_sha256 == hashlib.sha256(expected_json.encode()).hexdigest()

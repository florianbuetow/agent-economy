"""MP-09 L2 integrity regressions for stored legacy continuation evidence."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.services.legacy_artifact_continuation import (
    validate_legacy_continuation,
)
from tests.fakes.mp09_legacy_continuation_fixture import (
    ACCEPTED_US,
    BOARD_KID,
    POSTER_ID,
    TASK_ID,
    WORKER_ID,
)
from tests.fakes.mp09_legacy_submission_fixture import (
    BODY_A,
    SUBMISSION_MANIFEST_ID,
    canonical_json,
    finalize_upload,
    full_snapshot,
    prepare_legacy_submission,
    ready_upload,
    reserve_upload,
    submission_request,
    upload_request,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_FORBIDDEN_NATIVE_FIELDS: dict[str, object] = {
    "base_request_sha256": "1" * 64,
    "input_manifest_id": SUBMISSION_MANIFEST_ID,
    "input_manifest_sha256": "2" * 64,
    "publication_contract_sha256": "3" * 64,
    "accepted_publication_contract_sha256": "4" * 64,
    "publication_operation_id": "op-forbidden-native-publication",
    "input_manifest_kind": "input",
    "input_actor_id": POSTER_ID,
}
_FORBIDDEN_NATIVE_UPDATES = {
    "base_request_sha256": (
        "UPDATE board_task_artifact_contracts SET base_request_sha256=? WHERE task_id=?"
    ),
    "input_manifest_id": (
        "UPDATE board_task_artifact_contracts SET input_manifest_id=? WHERE task_id=?"
    ),
    "input_manifest_sha256": (
        "UPDATE board_task_artifact_contracts SET input_manifest_sha256=? WHERE task_id=?"
    ),
    "publication_contract_sha256": (
        "UPDATE board_task_artifact_contracts SET publication_contract_sha256=? WHERE task_id=?"
    ),
    "accepted_publication_contract_sha256": (
        "UPDATE board_task_artifact_contracts SET accepted_publication_contract_sha256=? "
        "WHERE task_id=?"
    ),
    "publication_operation_id": (
        "UPDATE board_task_artifact_contracts SET publication_operation_id=? WHERE task_id=?"
    ),
    "input_manifest_kind": (
        "UPDATE board_task_artifact_contracts SET input_manifest_kind=? WHERE task_id=?"
    ),
    "input_actor_id": ("UPDATE board_task_artifact_contracts SET input_actor_id=? WHERE task_id=?"),
}


def _assert_error(raised: pytest.ExceptionInfo[ServiceError], status: int, error: str) -> None:
    assert (raised.value.status_code, raised.value.error) == (status, error)


def _integrity_rows(fixture: Any) -> tuple[tuple[Any, ...], ...]:
    queries = (
        (
            "SELECT * FROM board_task_artifact_contracts WHERE task_id=?",
            (TASK_ID,),
        ),
        (
            "SELECT * FROM board_legacy_artifact_observations WHERE task_id=?",
            (TASK_ID,),
        ),
        ("SELECT * FROM bank_escrow WHERE task_id=?", (TASK_ID,)),
    )
    values: list[tuple[Any, ...]] = []
    for sql, parameters in queries:
        row = fixture.connection.execute(sql, parameters).fetchone()
        assert row is not None
        values.append(tuple(row))
    return tuple(values)


def _corrupt_forbidden_native_field(fixture: Any, field: str) -> None:
    assert field in _FORBIDDEN_NATIVE_FIELDS
    assert field in _FORBIDDEN_NATIVE_UPDATES
    fixture.connection.commit()
    fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
    fixture.connection.execute(
        _FORBIDDEN_NATIVE_UPDATES[field],
        (_FORBIDDEN_NATIVE_FIELDS[field], TASK_ID),
    )
    fixture.connection.commit()
    fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
    assert fixture.connection.execute("PRAGMA ignore_check_constraints").fetchone()[0] == 0
    assert fixture.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _corrupt_copied_observation_status(fixture: Any) -> None:
    fixture.connection.commit()
    fixture.connection.execute("PRAGMA ignore_check_constraints=ON")
    fixture.connection.execute(
        "UPDATE board_task_artifact_contracts SET "
        "legacy_acceptance_observation_status='unknown' WHERE task_id=?",
        (TASK_ID,),
    )
    fixture.connection.commit()
    fixture.connection.execute("PRAGMA ignore_check_constraints=OFF")
    assert fixture.connection.execute("PRAGMA ignore_check_constraints").fetchone()[0] == 0


def _coherent_observation_rewrite(
    fixture: Any,
    *,
    escrow_status: str | None,
    lifecycle_status: str | None,
) -> str:
    row = fixture.connection.execute(
        "SELECT canonical_observation_json FROM board_legacy_artifact_observations WHERE task_id=?",
        (TASK_ID,),
    ).fetchone()
    assert row is not None and isinstance(row[0], str)
    decoded: object = json.loads(row[0])
    assert isinstance(decoded, dict)
    if escrow_status is not None:
        decoded["escrow_status"] = escrow_status
        fixture.connection.execute(
            "UPDATE bank_escrow SET status=? WHERE task_id=?",
            (escrow_status, TASK_ID),
        )
    if lifecycle_status is not None:
        decoded["lifecycle_status"] = lifecycle_status
        fixture.connection.execute(
            "UPDATE board_tasks SET status=? WHERE task_id=?",
            (lifecycle_status, TASK_ID),
        )
    rendered = canonical_json(decoded)
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    fixture.connection.execute(
        "UPDATE board_legacy_artifact_observations SET canonical_observation_json=?,"
        "observation_sha256=? WHERE task_id=?",
        (rendered, digest, TASK_ID),
    )
    fixture.connection.execute(
        "UPDATE board_task_artifact_contracts SET legacy_acceptance_observation_sha256=? "
        "WHERE task_id=?",
        (digest, TASK_ID),
    )
    fixture.connection.commit()
    return digest


def _rebind_upload_rows(fixture: Any, digest: str) -> None:
    rows = fixture.connection.execute(
        "SELECT upload_operation_id,canonical_request_json FROM board_artifact_uploads "
        "ORDER BY upload_operation_id"
    ).fetchall()
    assert rows
    for row in rows:
        assert isinstance(row[1], str)
        decoded: object = json.loads(row[1])
        assert isinstance(decoded, dict)
        decoded["accepted_binding_sha256"] = digest
        rendered = canonical_json(decoded)
        request_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        fixture.connection.execute(
            "UPDATE board_artifact_uploads SET canonical_request_json=?,request_sha256=?,"
            "accepted_binding_sha256=? WHERE upload_operation_id=?",
            (rendered, request_sha256, digest, row[0]),
        )
    fixture.connection.commit()


@pytest.mark.parametrize("field", sorted(_FORBIDDEN_NATIVE_FIELDS))
@pytest.mark.parametrize("entrypoint", ["reserve", "finalize", "submission"])
def test_every_forbidden_native_field_invalidates_v0_continuation(
    tmp_path: Path,
    field: str,
    entrypoint: str,
) -> None:
    fixture = prepare_legacy_submission(
        tmp_path / f"forbidden-{entrypoint}-{field}.sqlite3",
        provenance="none",
    )
    try:
        request = upload_request(fixture, 1, body=BODY_A)
        reserved: dict[str, Any] | None = None
        if entrypoint == "finalize":
            reserved = reserve_upload(fixture, request)
            fixture.clock.now_us = ACCEPTED_US + 10_000_000
        elif entrypoint == "submission":
            ready_upload(fixture, 1, body=BODY_A)
        _corrupt_forbidden_native_field(fixture, field)
        integrity_before = _integrity_rows(fixture)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            if entrypoint == "reserve":
                reserve_upload(fixture, request)
            elif entrypoint == "finalize":
                assert reserved is not None
                finalize_upload(fixture, request, reserved)
            else:
                fixture.operations.claim_operation(submission_request(fixture, 1), BOARD_KID)
        _assert_error(raised, 500, "legacy_observation_invalid")
        assert _integrity_rows(fixture) == integrity_before
        if entrypoint != "finalize":
            assert full_snapshot(fixture.connection) == before
        else:
            upload = fixture.connection.execute(
                "SELECT state,current_attempt_id,finalized_at_us FROM board_artifact_uploads "
                "WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()
            attempt = fixture.connection.execute(
                "SELECT state,failure_code,updated_at_us FROM board_artifact_upload_attempts "
                "WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()
            assert upload is not None and tuple(upload) == (
                "orphaned",
                None,
                ACCEPTED_US + 10_000_000,
            )
            assert attempt is not None and tuple(attempt) == (
                "orphaned",
                "legacy_observation_invalid",
                ACCEPTED_US + 10_000_000,
            )
    finally:
        fixture.close()


@pytest.mark.parametrize("escrow_status", ["released", "split"])
@pytest.mark.parametrize("entrypoint", ["reserve", "finalize", "submission"])
def test_coherent_nonlocked_escrow_never_authorizes_continuation(
    tmp_path: Path,
    escrow_status: str,
    entrypoint: str,
) -> None:
    fixture = prepare_legacy_submission(
        tmp_path / f"coherent-{entrypoint}-{escrow_status}.sqlite3",
        provenance="none",
    )
    try:
        request = upload_request(fixture, 1, body=BODY_A)
        reserved: dict[str, Any] | None = None
        if entrypoint == "finalize":
            reserved = reserve_upload(fixture, request)
            fixture.clock.now_us = ACCEPTED_US + 20_000_000
        elif entrypoint == "submission":
            ready_upload(fixture, 1, body=BODY_A)
        digest = _coherent_observation_rewrite(
            fixture,
            escrow_status=escrow_status,
            lifecycle_status=None,
        )
        if entrypoint != "reserve":
            _rebind_upload_rows(fixture, digest)
        integrity_before = _integrity_rows(fixture)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            if entrypoint == "reserve":
                reserve_upload(
                    fixture,
                    upload_request(fixture, 2, body=BODY_A, binding_sha256=digest),
                )
            elif entrypoint == "finalize":
                assert reserved is not None
                finalize_upload(fixture, request, reserved)
            else:
                fixture.operations.claim_operation(
                    submission_request(fixture, 1, binding_sha256=digest),
                    BOARD_KID,
                )
        _assert_error(raised, 500, "legacy_observation_invalid")
        assert _integrity_rows(fixture) == integrity_before
        if entrypoint != "finalize":
            assert full_snapshot(fixture.connection) == before
        else:
            upload = fixture.connection.execute(
                "SELECT state,current_attempt_id,finalized_at_us FROM board_artifact_uploads "
                "WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()
            attempt = fixture.connection.execute(
                "SELECT state,failure_code,updated_at_us FROM board_artifact_upload_attempts "
                "WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()
            assert upload is not None and tuple(upload) == (
                "orphaned",
                None,
                ACCEPTED_US + 20_000_000,
            )
            assert attempt is not None and tuple(attempt) == (
                "orphaned",
                "legacy_observation_invalid",
                ACCEPTED_US + 20_000_000,
            )
    finally:
        fixture.close()


@pytest.mark.parametrize("entrypoint", ["reserve", "finalize", "submission"])
def test_unknown_copied_observation_status_is_invalid_not_unavailable(
    tmp_path: Path,
    entrypoint: str,
) -> None:
    fixture = prepare_legacy_submission(
        tmp_path / f"unknown-status-{entrypoint}.sqlite3",
        provenance="none",
    )
    try:
        request = upload_request(fixture, 1, body=BODY_A)
        reserved: dict[str, Any] | None = None
        if entrypoint == "finalize":
            reserved = reserve_upload(fixture, request)
            fixture.clock.now_us = ACCEPTED_US + 30_000_000
        elif entrypoint == "submission":
            ready_upload(fixture, 1, body=BODY_A)
        _corrupt_copied_observation_status(fixture)
        integrity_before = _integrity_rows(fixture)
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            if entrypoint == "reserve":
                reserve_upload(fixture, request)
            elif entrypoint == "finalize":
                assert reserved is not None
                finalize_upload(fixture, request, reserved)
            else:
                fixture.operations.claim_operation(submission_request(fixture, 1), BOARD_KID)
        _assert_error(raised, 500, "legacy_observation_invalid")
        assert _integrity_rows(fixture) == integrity_before
        if entrypoint != "finalize":
            assert full_snapshot(fixture.connection) == before
        else:
            upload = fixture.connection.execute(
                "SELECT state,current_attempt_id,finalized_at_us FROM board_artifact_uploads "
                "WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()
            attempt = fixture.connection.execute(
                "SELECT state,failure_code,updated_at_us FROM board_artifact_upload_attempts "
                "WHERE upload_operation_id=?",
                (request.upload_operation_id,),
            ).fetchone()
            assert upload is not None and tuple(upload) == (
                "orphaned",
                None,
                ACCEPTED_US + 30_000_000,
            )
            assert attempt is not None and tuple(attempt) == (
                "orphaned",
                "legacy_observation_invalid",
                ACCEPTED_US + 30_000_000,
            )
    finally:
        fixture.close()


def test_shared_validator_rejects_coherent_submitted_lifecycle(tmp_path: Path) -> None:
    fixture = prepare_legacy_submission(tmp_path / "coherent-submitted.sqlite3", provenance="none")
    try:
        digest = _coherent_observation_rewrite(
            fixture,
            escrow_status=None,
            lifecycle_status="submitted",
        )
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            validate_legacy_continuation(
                fixture.connection.cursor(),
                task_id=TASK_ID,
                worker_id=WORKER_ID,
                expected_lifecycle_version=1,
                accepted_binding_kind="legacy_accepted_continuation",
                accepted_binding_sha256=digest,
            )
        _assert_error(raised, 500, "legacy_observation_invalid")
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()


def test_shared_validator_accepts_real_l1_accepted_locked_control(tmp_path: Path) -> None:
    fixture = prepare_legacy_submission(tmp_path / "accepted-locked.sqlite3", provenance="none")
    try:
        observation = validate_legacy_continuation(
            fixture.connection.cursor(),
            task_id=TASK_ID,
            worker_id=WORKER_ID,
            expected_lifecycle_version=1,
            accepted_binding_kind=fixture.binding_kind,
            accepted_binding_sha256=fixture.binding_sha256,
        )
        assert observation.observation_status == "verified"
        assert observation.values["lifecycle_status"] == "accepted"
        assert observation.values["escrow_status"] == "locked"
        assert fixture.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        fixture.close()


@pytest.mark.parametrize(
    ("tamper", "expected_error"),
    [
        ("missing_contract", "artifact_contract_missing"),
        ("changed_request_binding", "accepted_binding_mismatch"),
    ],
)
def test_missing_contract_and_request_binding_precedence_remain_distinct(
    tmp_path: Path,
    tamper: str,
    expected_error: str,
) -> None:
    fixture = prepare_legacy_submission(
        tmp_path / f"precedence-{tamper}.sqlite3",
        provenance="none",
    )
    try:
        request = upload_request(fixture, 1, body=BODY_A)
        if tamper == "missing_contract":
            fixture.connection.execute(
                "DELETE FROM board_task_artifact_contracts WHERE task_id=?",
                (TASK_ID,),
            )
        else:
            request = upload_request(
                fixture,
                1,
                body=BODY_A,
                binding_sha256="f" * 64,
            )
        fixture.connection.commit()
        before = full_snapshot(fixture.connection)
        with pytest.raises(ServiceError) as raised:
            reserve_upload(fixture, request)
        _assert_error(raised, 409, expected_error)
        assert full_snapshot(fixture.connection) == before
    finally:
        fixture.close()

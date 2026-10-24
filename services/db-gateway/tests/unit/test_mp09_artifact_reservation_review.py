"""Review regressions for the bounded MP-09 artifact reservation kernel."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_v6_artifact_fixture import (
    BOARD_KID,
    EXECUTION_CUTOFF_US,
    LEGACY_BINDING,
    NATIVE_BINDING,
    TASK_ID,
    MutableClock,
    database_snapshot,
    explicit_policy,
    make_writer,
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


def _operation(number: int) -> str:
    return f"20000000-0000-4000-8000-{number:012d}"


def _request(
    api: Any,
    *,
    number: int = 1,
    binding_kind: str = "publication_contract",
    binding_sha256: str = NATIVE_BINDING,
    declared_size_bytes: int | None = None,
) -> Any:
    payload = upload_payload(
        operation_id=_operation(number),
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
        binding_kind=binding_kind,
        binding_sha256=binding_sha256,
    )
    if declared_size_bytes is not None:
        payload["declared_size_bytes"] = declared_size_bytes
    return api.requests.parse_artifact_upload_request(payload)


def _open_seeded(
    tmp_path: Path,
    *,
    binding_kind: str = "publication_contract",
    binding_sha256: str = NATIVE_BINDING,
) -> tuple[Any, sqlite3.Connection, MutableClock, Any]:
    api = require_v6_api()
    connection = open_v6_database(tmp_path / "artifact-review.sqlite3")
    seed_accepted_task(
        connection,
        task_id=_TASK_ID,
        manifest_id=_MANIFEST_ID,
        binding_kind=binding_kind,
        binding_sha256=binding_sha256,
    )
    clock = MutableClock()
    writer = make_writer(api, connection, clock, explicit_policy(api))
    return api, connection, clock, writer


def _service_error(call: Any) -> ServiceError:
    with pytest.raises(ServiceError) as raised:
        call()
    return raised.value


def test_reservation_caps_absolute_and_initial_lease_at_near_task_cutoff(
    tmp_path: Path,
) -> None:
    api, connection, clock, writer = _open_seeded(tmp_path)
    try:
        clock.now_us = EXECUTION_CUTOFF_US - 120_000_000
        request = _request(api)
        reserved = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        assert reserved["state"] == "reserved"
        assert reserved["attempt"]["absolute_upload_deadline_us"] == EXECUTION_CUTOFF_US
        assert reserved["attempt"]["lease_expires_at_us"] == EXECUTION_CUTOFF_US
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_reservation_at_or_after_task_cutoff_rejects_without_mutation(tmp_path: Path) -> None:
    api, connection, clock, writer = _open_seeded(tmp_path)
    try:
        request = _request(api)
        for now_us in (EXECUTION_CUTOFF_US, EXECUTION_CUTOFF_US + 1):
            clock.now_us = now_us
            before = database_snapshot(connection)
            error = _service_error(
                lambda: writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
            )
            assert (error.status_code, error.error) == (409, "upload_deadline_exceeded")
            assert database_snapshot(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("drift", "expected_error"),
    [
        ("version", "task_version_conflict"),
        ("status", "invalid_status"),
        ("binding", "accepted_binding_mismatch"),
        ("manifest", "manifest_mismatch"),
    ],
)
def test_expired_exact_takeover_revalidates_current_authority_before_new_generation(
    tmp_path: Path,
    drift: str,
    expected_error: str,
) -> None:
    api, connection, clock, writer = _open_seeded(tmp_path)
    try:
        request = _request(api)
        first = writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        clock.now_us = int(first["attempt"]["lease_expires_at_us"]) + 1
        if drift == "version":
            connection.execute(
                "UPDATE board_task_contracts SET lifecycle_version=2 WHERE task_id=?",
                (_TASK_ID,),
            )
        elif drift == "status":
            connection.execute(
                "UPDATE board_tasks SET status='submitted' WHERE task_id=?", (_TASK_ID,)
            )
        elif drift == "binding":
            connection.execute(
                "UPDATE board_task_artifact_contracts "
                "SET accepted_publication_contract_sha256=? WHERE task_id=?",
                ("f" * 64, _TASK_ID),
            )
        else:
            connection.execute(
                "UPDATE board_artifact_manifests SET state='closed_unsubmitted' "
                "WHERE manifest_id=?",
                (_MANIFEST_ID,),
            )
        connection.commit()
        before = database_snapshot(connection)

        error = _service_error(
            lambda: writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        )
        assert (error.status_code, error.error) == (409, expected_error)
        assert database_snapshot(connection) == before
        attempts = connection.execute(
            "SELECT generation,state FROM board_artifact_upload_attempts "
            "WHERE upload_operation_id=? ORDER BY generation",
            (request.upload_operation_id,),
        ).fetchall()
        assert [tuple(row) for row in attempts] == [(1, "active")]
    finally:
        connection.close()


def test_declared_file_size_limit_returns_413_without_mutation(tmp_path: Path) -> None:
    api, connection, _clock, writer = _open_seeded(tmp_path)
    try:
        request = _request(api, declared_size_bytes=33)
        before = database_snapshot(connection)
        error = _service_error(
            lambda: writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        )
        assert (error.status_code, error.error) == (413, "file_too_large")
        assert database_snapshot(connection) == before
    finally:
        connection.close()


def test_schema_supports_open_legacy_contract_without_invented_acceptance_history(
    tmp_path: Path,
) -> None:
    verified = open_v6_database(tmp_path / "verified-legacy.sqlite3")
    try:
        seed_accepted_task(
            verified,
            task_id=_TASK_ID,
            manifest_id=_MANIFEST_ID,
            binding_kind="legacy_accepted_continuation",
            binding_sha256=LEGACY_BINDING,
        )
        accepted = verified.execute(
            "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
            "legacy_acceptance_observation_sha256,legacy_acceptance_observation_status "
            "FROM board_task_artifact_contracts WHERE task_id=?",
            (_TASK_ID,),
        ).fetchone()
        assert accepted is not None
        assert tuple(accepted) == (
            0,
            "unknown_pre_v6",
            "legacy_accepted_continuation",
            LEGACY_BINDING,
            "verified",
        )
    finally:
        verified.close()

    connection = open_v6_database(tmp_path / "open-legacy.sqlite3")
    try:
        connection.execute(
            "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) "
            "VALUES ('poster-open','Poster','ed25519:poster-open','2099-01-01T23:58:00Z')"
        )
        connection.execute(
            "INSERT INTO bank_accounts (account_id,balance,created_at) "
            "VALUES ('poster-open',0,'2099-01-01T23:58:01Z')"
        )
        connection.execute(
            "INSERT INTO bank_escrow (escrow_id,payer_account_id,amount,task_id,status,created_at) "
            "VALUES ('escrow-open','poster-open',100,'task-open','locked',"
            "'2099-01-01T23:59:00Z')"
        )
        connection.execute(
            "INSERT INTO board_tasks (task_id,poster_id,title,spec,reward,status,"
            "bidding_deadline_seconds,deadline_seconds,review_deadline_seconds,"
            "bidding_deadline,escrow_id,created_at) VALUES "
            "('task-open','poster-open','Open legacy','No invented inputs.',100,'open',"
            "60,3600,60,'2099-01-02T00:01:00Z','escrow-open','2099-01-01T23:59:00Z')"
        )
        connection.execute(
            "INSERT INTO board_task_contracts (task_id,lifecycle_version,"
            "timing_contract_version,acceptance_window_seconds,bidding_cutoff_us,"
            "acceptance_cutoff_us,execution_cutoff_us,review_cutoff_us,accepted_reward,"
            "accepted_bid_amount) VALUES "
            "('task-open',0,2,60,4070995260000000,4070995320000000,NULL,NULL,NULL,NULL)"
        )
        connection.execute(
            "INSERT INTO board_task_artifact_contracts (task_id,artifact_contract_version,"
            "input_provenance,accepted_binding_kind,base_request_sha256,input_manifest_id,"
            "input_manifest_sha256,publication_contract_sha256,"
            "accepted_publication_contract_sha256,legacy_acceptance_observation_sha256,"
            "legacy_acceptance_observation_status,source_publication_request_sha256) "
            "VALUES ('task-open',0,'unknown_pre_v6',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)"
        )
        connection.commit()
        row = connection.execute(
            "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
            "legacy_acceptance_observation_sha256,legacy_acceptance_observation_status,"
            "input_manifest_id FROM board_task_artifact_contracts WHERE task_id='task-open'"
        ).fetchone()
        assert row is not None
        assert tuple(row) == (0, "unknown_pre_v6", None, None, None, None)
        manifest_count = connection.execute(
            "SELECT COUNT(*) FROM board_artifact_manifests WHERE task_id='task-open'"
        ).fetchone()
        assert manifest_count is not None
        assert tuple(manifest_count) == (0,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_incomplete_legacy_observation_blocks_matching_continuation_hash(
    tmp_path: Path,
) -> None:
    api, connection, _clock, writer = _open_seeded(
        tmp_path,
        binding_kind="legacy_accepted_continuation",
        binding_sha256=LEGACY_BINDING,
    )
    try:
        connection.execute(
            "UPDATE board_task_artifact_contracts "
            "SET legacy_acceptance_observation_status='incomplete_corrupt' WHERE task_id=?",
            (_TASK_ID,),
        )
        connection.execute(
            "UPDATE board_legacy_artifact_observations "
            "SET observation_status='incomplete_corrupt' WHERE task_id=?",
            (_TASK_ID,),
        )
        connection.commit()
        request = _request(
            api,
            binding_kind="legacy_accepted_continuation",
            binding_sha256=LEGACY_BINDING,
        )
        before = database_snapshot(connection)
        error = _service_error(
            lambda: writer.reserve_upload(request, verified_caller_kid=BOARD_KID)
        )
        assert (error.status_code, error.error) == (409, "legacy_continuation_unavailable")
        assert database_snapshot(connection) == before
    finally:
        connection.close()

"""Critical review regressions for V2 prepublication ownership."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.exceptions import ServiceError

from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    FREEZE_ID,
    NOW_US,
    POSTER_ID,
    PUBLICATION_ID,
    TASK_ID,
    admission_payload,
    canonical_sha256,
    freeze_payload,
    full_snapshot,
    make_writer,
    open_prototype_database,
    publish_payload,
    require_prepublication_api,
    seed_poster,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _open_writer(tmp_path: Path) -> tuple[Any, sqlite3.Connection, Any]:
    api = require_prepublication_api()
    connection = open_prototype_database(tmp_path / "input-publication-review.sqlite3")
    seed_poster(connection)
    return api, connection, make_writer(api, connection)


def _admit_zero(api: Any, writer: Any) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_publication_admission_v2(admission_payload(expected_input_count=0))
    return request, writer.admit(request, verified_caller_kid=BOARD_KID)


def _freeze_zero(api: Any, writer: Any, input_manifest_id: str) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_input_manifest_freeze_v2(freeze_payload(input_manifest_id))
    return request, writer.freeze_inputs(request, verified_caller_kid=BOARD_KID)


def _authorize_publish(
    api: Any,
    writer: Any,
    publication_contract_sha256: str,
) -> tuple[Any, dict[str, Any]]:
    request = api.requests.parse_publication_publish_v2(
        publish_payload(publication_contract_sha256)
    )
    return request, writer.authorize_publish(request, verified_caller_kid=BOARD_KID)


def _seed_valid_public_task_owner_graph(connection: sqlite3.Connection) -> None:
    """Create a valid task whose worker key matches the manifest's poster actor."""
    connection.execute(
        "INSERT INTO bank_accounts (account_id,balance,created_at) VALUES (?,?,?)",
        (POSTER_ID, 0, "2099-01-01T23:58:01Z"),
    )
    connection.execute(
        "INSERT INTO bank_escrow (escrow_id,payer_account_id,amount,task_id,status,created_at) "
        "VALUES ('escrow-input-review',?,100,?,'locked','2099-01-01T23:59:00Z')",
        (POSTER_ID, TASK_ID),
    )
    connection.execute(
        "INSERT INTO board_tasks (task_id,poster_id,title,spec,reward,status,"
        "bidding_deadline_seconds,deadline_seconds,review_deadline_seconds,bidding_deadline,"
        "execution_deadline,escrow_id,worker_id,accepted_at,created_at) VALUES "
        "(?,?, 'Supporting task','Validate input discriminator.',100,'accepted',60,3600,60,"
        "'2099-01-02T00:01:00Z','2099-01-02T01:00:00Z','escrow-input-review',?,"
        "'2099-01-02T00:00:00Z','2099-01-01T23:59:00Z')",
        (TASK_ID, POSTER_ID, POSTER_ID),
    )
    connection.execute(
        "INSERT INTO board_task_contracts (task_id,lifecycle_version,timing_contract_version,"
        "acceptance_window_seconds,bidding_cutoff_us,acceptance_cutoff_us,execution_cutoff_us,"
        "review_cutoff_us,accepted_reward,accepted_bid_amount) VALUES "
        "(?,1,2,60,?,?,?,NULL,100,80)",
        (TASK_ID, NOW_US + 60_000_000, NOW_US + 120_000_000, NOW_US + 3_600_000_000),
    )
    connection.commit()
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_awaiting_lock_reconcile_reads_bank_without_any_mutation(
    tmp_path: Path,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        admission_request, admitted = _admit_zero(api, writer)
        assert admitted["base_request_sha256"] == admission_request.request_sha256
        input_manifest_id = admitted["input_manifest_id"]
        assert isinstance(input_manifest_id, str) and input_manifest_id
        _freeze_request, frozen = _freeze_zero(api, writer, input_manifest_id)
        _publish_request, authorized = _authorize_publish(
            api,
            writer,
            str(frozen["publication_contract_sha256"]),
        )
        assert authorized["publication_state"] == "awaiting_lock"
        before = full_snapshot(connection)
        bank_reads: list[str] = []

        def allow_bank_reads_and_deny_writes(
            action: int,
            table: str | None,
            _column: str | None,
            _database: str | None,
            _source: str | None,
        ) -> int:
            if isinstance(table, str) and table.startswith("bank_"):
                if action == sqlite3.SQLITE_READ:
                    bank_reads.append(table)
                elif action in (
                    sqlite3.SQLITE_INSERT,
                    sqlite3.SQLITE_UPDATE,
                    sqlite3.SQLITE_DELETE,
                ):
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(allow_bank_reads_and_deny_writes)
        try:
            result = writer.reconcile(PUBLICATION_ID, verified_caller_kid=BOARD_KID)
        finally:
            connection.set_authorizer(None)
        assert result == authorized
        assert "bank_operation_receipts" in bank_reads
        assert full_snapshot(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("phase", "boundary"),
    [
        ("admit", "journal"),
        ("admit", "manifest"),
        ("freeze", "journal"),
        ("freeze", "manifest"),
    ],
)
def test_publication_and_manifest_persistence_failures_roll_back_atomically(
    tmp_path: Path,
    phase: str,
    boundary: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        if phase == "freeze":
            _request, admitted = _admit_zero(api, writer)
            input_manifest_id = admitted["input_manifest_id"]
            assert isinstance(input_manifest_id, str) and input_manifest_id
            request = api.requests.parse_input_manifest_freeze_v2(freeze_payload(input_manifest_id))
        else:
            request = api.requests.parse_publication_admission_v2(
                admission_payload(expected_input_count=0)
            )

        if phase == "admit" and boundary == "journal":
            connection.execute(
                "CREATE TRIGGER inject_admission_journal_failure "
                "BEFORE INSERT ON board_task_publications BEGIN "
                "SELECT RAISE(ABORT,'injected admission journal failure'); END"
            )
        elif phase == "admit":
            connection.execute(
                "CREATE TRIGGER inject_admission_manifest_failure "
                "BEFORE INSERT ON board_artifact_manifests BEGIN "
                "SELECT RAISE(ABORT,'injected admission manifest failure'); END"
            )
        elif boundary == "journal":
            connection.execute(
                "CREATE TRIGGER inject_freeze_journal_failure "
                "BEFORE UPDATE OF state ON board_task_publications "
                "WHEN NEW.state='awaiting_publish' BEGIN "
                "SELECT RAISE(ABORT,'injected freeze journal failure'); END"
            )
        else:
            connection.execute(
                "CREATE TRIGGER inject_freeze_manifest_failure "
                "BEFORE UPDATE OF state ON board_artifact_manifests "
                "WHEN NEW.state='frozen' BEGIN "
                "SELECT RAISE(ABORT,'injected freeze manifest failure'); END"
            )
        connection.commit()
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            if phase == "admit":
                writer.admit(request, verified_caller_kid=BOARD_KID)
            else:
                writer.freeze_inputs(request, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            500,
            "publication_persistence_failed",
        )
        assert full_snapshot(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("freeze_operation_id", FREEZE_ID),
        ("canonical_freeze_request_json", "{}"),
        ("publish_operation_id", "op-60000000-0000-4000-8000-000000000003"),
        ("canonical_publish_request_json", "{}"),
    ],
)
def test_direct_sql_rejects_partial_v2_publication_state_groups(
    tmp_path: Path,
    column: str,
    value: object,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _request, _admitted = _admit_zero(api, writer)
        before = full_snapshot(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                f"UPDATE board_task_publications SET {column}=? WHERE operation_id=?",  # nosec B608
                (value, PUBLICATION_ID),
            )
        connection.rollback()
        assert full_snapshot(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize(
    "invalid_shape",
    ["missing_publication", "wrong_publication", "public_task", "second_input"],
)
def test_direct_sql_rejects_invalid_input_manifest_ownership_shapes(
    tmp_path: Path,
    invalid_shape: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _request, admitted = _admit_zero(api, writer)
        original_manifest_id = admitted["input_manifest_id"]
        assert isinstance(original_manifest_id, str) and original_manifest_id
        if invalid_shape == "public_task":
            _seed_valid_public_task_owner_graph(connection)
        before = full_snapshot(connection)
        with pytest.raises(sqlite3.IntegrityError):
            if invalid_shape == "second_input":
                connection.execute(
                    "INSERT INTO board_artifact_manifests "
                    "(manifest_id,task_id,kind,actor_id,format_version,state,"
                    "canonical_manifest_json,manifest_sha256,frozen_at_us,"
                    "publication_operation_id,public_task_id) "
                    "VALUES ('manifest-input-second',?,'input',?,1,'draft',"
                    "NULL,NULL,NULL,?,NULL)",
                    (TASK_ID, POSTER_ID, PUBLICATION_ID),
                )
            elif invalid_shape == "missing_publication":
                connection.execute(
                    "UPDATE board_artifact_manifests SET publication_operation_id=NULL "
                    "WHERE manifest_id=?",
                    (original_manifest_id,),
                )
            elif invalid_shape == "wrong_publication":
                connection.execute(
                    "UPDATE board_artifact_manifests SET publication_operation_id=? "
                    "WHERE manifest_id=?",
                    (
                        "op-60000000-0000-4000-8000-000000000099",
                        original_manifest_id,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE board_artifact_manifests SET public_task_id=? WHERE manifest_id=?",
                    (TASK_ID, original_manifest_id),
                )
        connection.rollback()
        assert full_snapshot(connection) == before
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM board_artifact_manifests WHERE manifest_id=?",
                (original_manifest_id,),
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()


def test_base_request_hash_is_exact_complete_canonical_admission_hash(tmp_path: Path) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        payload = admission_payload(expected_input_count=0)
        request = api.requests.parse_publication_admission_v2(payload)
        admitted = writer.admit(request, verified_caller_kid=BOARD_KID)
        expected_hash = canonical_sha256(payload)
        assert request.request_sha256 == expected_hash
        assert request.base_request_sha256 == expected_hash
        assert admitted["base_request_sha256"] == expected_hash
        row = connection.execute(
            "SELECT request_sha256,base_request_sha256,canonical_request_json "
            "FROM board_task_publications WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert row is not None
        assert tuple(row) == (expected_hash, expected_hash, request.canonical_request_json)
    finally:
        connection.close()


def test_owner_get_hides_foreign_actor_and_missing_identity_with_same_not_found(
    tmp_path: Path,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _request, _admitted = _admit_zero(api, writer)
        before = full_snapshot(connection)
        errors: list[ServiceError] = []
        for operation_id, actor_id in (
            (PUBLICATION_ID, "poster-other"),
            ("op-60000000-0000-4000-8000-000000000099", POSTER_ID),
        ):
            with pytest.raises(ServiceError) as raised:
                writer.get(
                    operation_id,
                    verified_caller_kid=BOARD_KID,
                    expected_actor_id=actor_id,
                )
            errors.append(raised.value)
        assert [(error.status_code, error.error, error.details) for error in errors] == [
            (404, "publication_not_found", {}),
            (404, "publication_not_found", {}),
        ]
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_cross_kind_operation_ids_and_lock_id_constraints_are_enforced(
    tmp_path: Path,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        lock_operation_id = str(admission_payload(expected_input_count=0)["lock_operation_id"])
        for parser, payload in (
            (
                api.requests.parse_input_manifest_freeze_v2,
                freeze_payload("manifest-parser-only", freeze_operation_id=lock_operation_id),
            ),
            (
                api.requests.parse_publication_publish_v2,
                publish_payload("a" * 64, publish_operation_id=lock_operation_id),
            ),
        ):
            with pytest.raises(ServiceError) as raised:
                parser(payload)
            assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")

        _request, admitted = _admit_zero(api, writer)
        input_manifest_id = admitted["input_manifest_id"]
        assert isinstance(input_manifest_id, str) and input_manifest_id
        _freeze_request, frozen = _freeze_zero(api, writer, input_manifest_id)
        publish = api.requests.parse_publication_publish_v2(
            publish_payload(
                str(frozen["publication_contract_sha256"]),
                publish_operation_id=FREEZE_ID,
            )
        )
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            writer.authorize_publish(publish, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (409, "operation_conflict")
        assert full_snapshot(connection) == before
    finally:
        connection.close()

"""T-126 red proof for actor-bound prepublication input manifests."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from db_gateway_service.task_publication_requests import parse_task_publication_request
from tests.fakes.mp09_input_publication_fixture import (
    BOARD_KID,
    FREEZE_ID,
    INPUT_MANIFEST_ID,
    NOW_US,
    POSTER_ID,
    PUBLICATION_ID,
    PUBLISH_ID,
    TASK_ID,
    admission_payload,
    canonical_json,
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


def _parse_admission(api: Any, payload: dict[str, object]) -> Any:
    return api.requests.parse_publication_admission_v2(payload)


def _parse_freeze(api: Any, payload: dict[str, object]) -> Any:
    return api.requests.parse_input_manifest_freeze_v2(payload)


def _parse_publish(api: Any, payload: dict[str, object]) -> Any:
    return api.requests.parse_publication_publish_v2(payload)


def _open_writer(tmp_path: Path) -> tuple[Any, sqlite3.Connection, Any]:
    api = require_prepublication_api()
    connection = open_prototype_database(tmp_path / "input-publication.sqlite3")
    seed_poster(connection)
    writer = make_writer(api, connection)
    return api, connection, writer


def _admit_zero(api: Any, writer: Any) -> tuple[Any, dict[str, Any]]:
    request = _parse_admission(api, admission_payload(expected_input_count=0))
    result = writer.admit(request, verified_caller_kid=BOARD_KID)
    return request, result


def _freeze_zero(
    api: Any,
    writer: Any,
    input_manifest_id: str,
) -> tuple[Any, dict[str, Any]]:
    request = _parse_freeze(api, freeze_payload(input_manifest_id))
    result = writer.freeze_inputs(request, verified_caller_kid=BOARD_KID)
    return request, result


def test_fixture_canonical_identities_and_v1_hash_semantics_are_exact() -> None:
    admission = admission_payload(expected_input_count=0)
    freeze = freeze_payload(INPUT_MANIFEST_ID)
    publish = publish_payload("a" * 64)
    assert set(admission) == {
        "protocol_version",
        "publication_operation_id",
        "task_id",
        "actor_kind",
        "actor_id",
        "action",
        "resource",
        "lock_operation_id",
        "title",
        "spec",
        "reward",
        "bidding_deadline_seconds",
        "acceptance_window_seconds",
        "execution_deadline_seconds",
        "review_deadline_seconds",
        "expected_input_count",
    }
    assert set(freeze) == {
        "protocol_version",
        "freeze_operation_id",
        "publication_operation_id",
        "task_id",
        "actor_id",
        "action",
        "resource",
        "input_manifest_id",
    }
    assert set(publish) == {
        "protocol_version",
        "publish_operation_id",
        "publication_operation_id",
        "task_id",
        "actor_id",
        "action",
        "resource",
        "lock_operation_id",
        "publication_contract_sha256",
    }
    assert len({PUBLICATION_ID, FREEZE_ID, PUBLISH_ID, admission["lock_operation_id"]}) == 4
    assert (
        canonical_sha256(admission)
        == hashlib.sha256(canonical_json(admission).encode("utf-8")).hexdigest()
    )
    assert (
        canonical_sha256(freeze)
        == hashlib.sha256(canonical_json(freeze).encode("utf-8")).hexdigest()
    )
    assert (
        canonical_sha256(publish)
        == hashlib.sha256(canonical_json(publish).encode("utf-8")).hexdigest()
    )

    v1_payload = {
        "protocol_version": 1,
        "operation_id": PUBLICATION_ID,
        "task_id": TASK_ID,
        "actor_kind": "agent",
        "actor_id": POSTER_ID,
        "lock_operation_id": admission["lock_operation_id"],
        "title": "V1 unchanged",
        "spec": "Preserve exact V1 semantic request identity.",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 60,
        "execution_deadline_seconds": 3600,
        "review_deadline_seconds": 60,
    }
    parsed_v1 = parse_task_publication_request(v1_payload)
    semantic_v1 = dict(v1_payload)
    del semantic_v1["operation_id"]
    assert parsed_v1.canonical_json == canonical_json(v1_payload)
    assert parsed_v1.request_sha256 == canonical_sha256(semantic_v1)


def test_publication_v6_schema_supports_input_owner_without_task_or_escrow(tmp_path: Path) -> None:
    connection = open_prototype_database(tmp_path / "publication-v6-schema.sqlite3")
    try:
        seed_poster(connection)
        assert (
            connection.execute("SELECT version FROM schema_metadata WHERE singleton=1").fetchone()[
                0
            ]
            == 5
        )
        manifest_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(board_artifact_manifests)")
        }
        assert {"publication_operation_id", "public_task_id"} <= manifest_columns
        publication_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(board_task_publications)")
        }
        assert {
            "request_protocol_version",
            "expected_input_count",
            "base_request_sha256",
            "freeze_operation_id",
            "publish_operation_id",
        } <= publication_columns
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM bank_escrow").fetchone()[0] == 0
    finally:
        connection.close()


def test_v2_admission_freeze_and_publish_parsers_are_strict_and_canonical() -> None:
    api = require_prepublication_api()
    cases = (
        (
            _parse_admission,
            admission_payload(expected_input_count=0),
            "canonical_request_json",
            "request_sha256",
        ),
        (
            _parse_freeze,
            freeze_payload(INPUT_MANIFEST_ID),
            "canonical_freeze_request_json",
            "freeze_request_sha256",
        ),
        (
            _parse_publish,
            publish_payload("a" * 64),
            "canonical_publish_request_json",
            "publish_request_sha256",
        ),
    )
    for parser, payload, canonical_field, hash_field in cases:
        parsed = parser(api, payload)
        assert getattr(parsed, canonical_field) == canonical_json(payload)
        assert getattr(parsed, hash_field) == canonical_sha256(payload)
        for missing in payload:
            incomplete = dict(payload)
            del incomplete[missing]
            with pytest.raises(ServiceError) as raised:
                parser(api, incomplete)
            assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")
        extra = dict(payload)
        extra["implicit_default"] = 1
        with pytest.raises(ServiceError) as raised:
            parser(api, extra)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")

    malformed_cases = (
        (_parse_admission, admission_payload(expected_input_count=True)),
        (_parse_admission, admission_payload(expected_input_count=-1)),
        (_parse_admission, admission_payload(expected_input_count=0, actor_kind=1)),
        (_parse_freeze, freeze_payload(INPUT_MANIFEST_ID, freeze_operation_id=1)),
        (_parse_freeze, freeze_payload(INPUT_MANIFEST_ID, action="freeze")),
        (_parse_publish, publish_payload("A" * 64)),
        (_parse_publish, publish_payload("f" * 63)),
        (_parse_publish, publish_payload("a" * 64, lock_operation_id=FREEZE_ID)),
    )
    for parser, malformed in malformed_cases:
        with pytest.raises(ServiceError) as raised:
            parser(api, malformed)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")

    pairwise_cases = (
        (
            _parse_admission,
            admission_payload(expected_input_count=0, lock_operation_id=PUBLICATION_ID),
        ),
        (
            _parse_freeze,
            freeze_payload(INPUT_MANIFEST_ID, freeze_operation_id=PUBLICATION_ID),
        ),
        (
            _parse_publish,
            publish_payload("a" * 64, publish_operation_id=PUBLICATION_ID),
        ),
    )
    for parser, malformed in pairwise_cases:
        with pytest.raises(ServiceError) as raised:
            parser(api, malformed)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")


def test_admission_rejects_expected_input_count_above_explicit_policy_without_mutation(
    tmp_path: Path,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        request = _parse_admission(api, admission_payload(expected_input_count=3))
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            writer.admit(request, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (400, "invalid_payload")
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_v2_admission_creates_only_publication_owned_draft_and_replays_exactly(
    tmp_path: Path,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        request = _parse_admission(api, admission_payload(expected_input_count=0))
        admitted = writer.admit(request, verified_caller_kid=BOARD_KID)
        assert admitted["publication_state"] == "awaiting_inputs"
        assert admitted["publication_operation_id"] == PUBLICATION_ID
        input_manifest_id = admitted["input_manifest_id"]
        assert isinstance(input_manifest_id, str) and input_manifest_id
        publication = connection.execute(
            "SELECT request_protocol_version,state,expected_input_count,base_request_sha256,"
            "freeze_operation_id,publish_operation_id,escrow_id,result_json "
            "FROM board_task_publications WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert publication is not None
        assert tuple(publication) == (
            2,
            "awaiting_inputs",
            0,
            request.base_request_sha256,
            None,
            None,
            None,
            None,
        )
        manifest = connection.execute(
            "SELECT task_id,kind,actor_id,publication_operation_id,public_task_id,state "
            "FROM board_artifact_manifests WHERE manifest_id=?",
            (input_manifest_id,),
        ).fetchone()
        assert manifest is not None
        assert tuple(manifest) == (
            TASK_ID,
            "input",
            POSTER_ID,
            PUBLICATION_ID,
            None,
            "draft",
        )
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM bank_escrow").fetchone()[0] == 0
        replay = writer.admit(request, verified_caller_kid=BOARD_KID)
        assert replay == admitted
        assert replay["input_manifest_id"] == input_manifest_id

        changed = _parse_admission(
            api,
            admission_payload(expected_input_count=0, title="Changed under same identity"),
        )
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            writer.admit(changed, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (409, "operation_conflict")
        assert full_snapshot(connection) == before
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        connection.close()


def test_publish_before_input_freeze_is_rejected_without_mutation(tmp_path: Path) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _request, _admitted = _admit_zero(api, writer)
        request = _parse_publish(api, publish_payload("a" * 64))
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            writer.authorize_publish(request, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (409, "invalid_status")
        assert full_snapshot(connection) == before
    finally:
        connection.close()


@pytest.mark.parametrize("command", ["admit", "freeze", "publish"])
def test_wrong_verified_caller_cannot_mutate_prepublication_state(
    tmp_path: Path,
    command: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        if command == "admit":
            request = _parse_admission(api, admission_payload(expected_input_count=0))
        else:
            _admission_request, admitted = _admit_zero(api, writer)
            input_manifest_id = admitted["input_manifest_id"]
            assert isinstance(input_manifest_id, str) and input_manifest_id
            if command == "freeze":
                request = _parse_freeze(api, freeze_payload(input_manifest_id))
            else:
                _freeze_request, frozen = _freeze_zero(api, writer, input_manifest_id)
                request = _parse_publish(
                    api,
                    publish_payload(frozen["publication_contract_sha256"]),
                )
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            if command == "admit":
                writer.admit(request, verified_caller_kid="gateway-court")
            elif command == "freeze":
                writer.freeze_inputs(request, verified_caller_kid="gateway-court")
            else:
                writer.authorize_publish(request, verified_caller_kid="gateway-court")
        assert (raised.value.status_code, raised.value.error) == (
            403,
            "operation_caller_mismatch",
        )
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_actor_bound_input_manifest_foreign_key_rejects_direct_owner_change(
    tmp_path: Path,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        connection.execute(
            "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) VALUES (?,?,?,?)",
            ("poster-other", "Other", "ed25519:poster-other", "2099-01-01T23:58:01Z"),
        )
        connection.commit()
        _request, admitted = _admit_zero(api, writer)
        input_manifest_id = admitted["input_manifest_id"]
        assert isinstance(input_manifest_id, str) and input_manifest_id
        before = full_snapshot(connection)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE board_artifact_manifests SET actor_id='poster-other' WHERE manifest_id=?",
                (input_manifest_id,),
            )
        connection.rollback()
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_zero_input_freeze_is_explicit_and_reaches_awaiting_publish_without_finance(
    tmp_path: Path,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        admission_request, admitted = _admit_zero(api, writer)
        input_manifest_id = admitted["input_manifest_id"]
        assert isinstance(input_manifest_id, str) and input_manifest_id
        freeze_request, frozen = _freeze_zero(api, writer, input_manifest_id)
        expected_manifest = {
            "format_version": 1,
            "manifest_id": input_manifest_id,
            "task_id": TASK_ID,
            "kind": "input",
            "actor_id": POSTER_ID,
            "artifacts": [],
        }
        manifest_json = canonical_json(expected_manifest)
        manifest_sha256 = canonical_sha256(expected_manifest)
        publication_contract_sha256 = canonical_sha256(
            {
                "artifact_contract_version": 1,
                "base_request_sha256": admission_request.base_request_sha256,
                "input_manifest_sha256": manifest_sha256,
            }
        )
        assert frozen["publication_state"] == "awaiting_publish"
        assert frozen["input_manifest_sha256"] == manifest_sha256
        assert frozen["publication_contract_sha256"] == publication_contract_sha256
        publication = connection.execute(
            "SELECT state,freeze_operation_id,canonical_freeze_request_json,"
            "freeze_request_sha256,input_manifest_sha256,publication_contract_sha256,"
            "inputs_frozen_at_us,publish_operation_id,escrow_id FROM board_task_publications "
            "WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert publication is not None
        assert tuple(publication) == (
            "awaiting_publish",
            FREEZE_ID,
            freeze_request.canonical_freeze_request_json,
            freeze_request.freeze_request_sha256,
            manifest_sha256,
            publication_contract_sha256,
            NOW_US,
            None,
            None,
        )
        manifest = connection.execute(
            "SELECT state,canonical_manifest_json,manifest_sha256,frozen_at_us "
            "FROM board_artifact_manifests WHERE manifest_id=?",
            (input_manifest_id,),
        ).fetchone()
        assert manifest is not None
        assert tuple(manifest) == ("frozen", manifest_json, manifest_sha256, NOW_US)
        replay = writer.freeze_inputs(freeze_request, verified_caller_kid=BOARD_KID)
        assert replay == frozen
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM bank_escrow").fetchone()[0] == 0
    finally:
        connection.close()


def test_separately_signed_publish_authorization_reaches_awaiting_lock_without_bank_rows(
    tmp_path: Path,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _admission_request, admitted = _admit_zero(api, writer)
        input_manifest_id = admitted["input_manifest_id"]
        assert isinstance(input_manifest_id, str) and input_manifest_id
        freeze_request, frozen = _freeze_zero(api, writer, input_manifest_id)
        request = _parse_publish(api, publish_payload(frozen["publication_contract_sha256"]))
        authorized = writer.authorize_publish(request, verified_caller_kid=BOARD_KID)
        assert authorized["publication_state"] == "awaiting_lock"
        publication = connection.execute(
            "SELECT state,publish_operation_id,canonical_publish_request_json,"
            "publish_request_sha256,publish_authorized_at_us,escrow_id,lock_request_sha256,"
            "result_json FROM board_task_publications WHERE operation_id=?",
            (PUBLICATION_ID,),
        ).fetchone()
        assert publication is not None
        assert tuple(publication) == (
            "awaiting_lock",
            PUBLISH_ID,
            request.canonical_publish_request_json,
            request.publish_request_sha256,
            NOW_US,
            None,
            None,
            None,
        )
        assert connection.execute("SELECT COUNT(*) FROM bank_accounts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM bank_escrow").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM board_tasks").fetchone()[0] == 0
        assert writer.authorize_publish(request, verified_caller_kid=BOARD_KID) == authorized
        assert writer.freeze_inputs(freeze_request, verified_caller_kid=BOARD_KID) == authorized

        changed_publish = _parse_publish(
            api,
            publish_payload("f" * 64),
        )
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            writer.authorize_publish(changed_publish, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (409, "operation_conflict")
        assert full_snapshot(connection) == before

        changed_freeze = _parse_freeze(
            api,
            freeze_payload(input_manifest_id, actor_id="poster-other"),
        )
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            writer.freeze_inputs(changed_freeze, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (409, "operation_conflict")
        assert full_snapshot(connection) == before
    finally:
        connection.close()


def test_passive_reconcile_owner_read_and_recovery_never_read_bank_tables(tmp_path: Path) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        _request, admitted = _admit_zero(api, writer)
        input_manifest_id = admitted["input_manifest_id"]
        assert isinstance(input_manifest_id, str) and input_manifest_id

        def deny_bank_reads(
            action: int,
            table: str | None,
            _column: str | None,
            _database: str | None,
            _source: str | None,
        ) -> int:
            if (
                action == sqlite3.SQLITE_READ
                and isinstance(table, str)
                and table.startswith("bank_")
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_bank_reads)
        try:
            awaiting_inputs = writer.reconcile(PUBLICATION_ID, verified_caller_kid=BOARD_KID)
            owner_view = writer.get(
                PUBLICATION_ID,
                verified_caller_kid=BOARD_KID,
                expected_actor_id=POSTER_ID,
            )
            recovery = writer.recovery("", 100, verified_caller_kid=BOARD_KID)
        finally:
            connection.set_authorizer(None)
        assert awaiting_inputs["publication_state"] == "awaiting_inputs"
        assert owner_view["publication_state"] == "awaiting_inputs"
        assert recovery == {"items": [], "next_after": None}

        _freeze_zero(api, writer, input_manifest_id)
        connection.set_authorizer(deny_bank_reads)
        try:
            awaiting_publish = writer.reconcile(PUBLICATION_ID, verified_caller_kid=BOARD_KID)
            recovery = writer.recovery("", 100, verified_caller_kid=BOARD_KID)
        finally:
            connection.set_authorizer(None)
        assert awaiting_publish["publication_state"] == "awaiting_publish"
        assert recovery == {"items": [], "next_after": None}
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("command", "changed_field", "changed_value", "expected_status", "expected_error"),
    [
        ("freeze", "actor_id", "poster-other", 403, "forbidden"),
        (
            "freeze",
            "task_id",
            "t-60000000-0000-4000-8000-000000000099",
            409,
            "publication_mismatch",
        ),
        ("freeze", "input_manifest_id", "manifest-other", 409, "manifest_mismatch"),
        ("publish", "actor_id", "poster-other", 403, "forbidden"),
        (
            "publish",
            "task_id",
            "t-60000000-0000-4000-8000-000000000099",
            409,
            "publication_mismatch",
        ),
        ("publish", "publication_contract_sha256", "f" * 64, 409, "publication_contract_mismatch"),
    ],
)
def test_wrong_prepublication_linkage_is_rejected_without_mutation(
    tmp_path: Path,
    command: str,
    changed_field: str,
    changed_value: object,
    expected_status: int,
    expected_error: str,
) -> None:
    api, connection, writer = _open_writer(tmp_path)
    try:
        connection.execute(
            "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) VALUES (?,?,?,?)",
            ("poster-other", "Other", "ed25519:poster-other", "2099-01-01T23:58:01Z"),
        )
        connection.commit()
        _request, admitted = _admit_zero(api, writer)
        input_manifest_id = admitted["input_manifest_id"]
        assert isinstance(input_manifest_id, str) and input_manifest_id
        if command == "freeze":
            payload = freeze_payload(input_manifest_id)
            payload[changed_field] = changed_value
            request = _parse_freeze(api, payload)
        else:
            _freeze_request, frozen = _freeze_zero(api, writer, input_manifest_id)
            payload = publish_payload(frozen["publication_contract_sha256"])
            payload[changed_field] = changed_value
            if changed_field in {"actor_id", "task_id"}:
                actor_id = str(payload["actor_id"])
                task_id = str(payload["task_id"])
                payload["lock_operation_id"] = operation_id_for(
                    "task-lock",
                    (task_id, actor_id),
                )
            request = _parse_publish(api, payload)
        before = full_snapshot(connection)
        with pytest.raises(ServiceError) as raised:
            if command == "freeze":
                writer.freeze_inputs(request, verified_caller_kid=BOARD_KID)
            else:
                writer.authorize_publish(request, verified_caller_kid=BOARD_KID)
        assert (raised.value.status_code, raised.value.error) == (
            expected_status,
            expected_error,
        )
        assert full_snapshot(connection) == before
    finally:
        connection.close()

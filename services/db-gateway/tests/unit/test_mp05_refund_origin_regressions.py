"""Regression proofs for immutable MP-05 refund origin and completion identity."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
from service_auth.financial_identity import operation_id_for
from service_commons.exceptions import ServiceError

from db_gateway_service.financial_requests import canonical_request
from db_gateway_service.migration import migrate_v3_to_v4
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_time import parse_utc_text_to_epoch_us
from tests.fakes.mp05_publication_migration_history import (
    LEGACY_REFUNDED_ESCROW_ID,
    V3Source,
    add_rich_cutover_history,
    create_funded_payer,
    lock_escrow,
    open_v3_writer,
    prepare_v3_source,
    prepare_v3_source_with_legacy_refund,
    publish_verified_backup,
    release_escrow,
)

pytestmark = pytest.mark.unit

_RESOLVED_AT = "2099-01-01T12:00:02.000000Z"


@dataclass(frozen=True, slots=True)
class _NativeTemplate:
    database: Path
    caller_kid: str
    existing_observation_ids: tuple[str, str]
    pending_observation_ids: tuple[str, str]


@dataclass(frozen=True, slots=True)
class _LegacyTemplate:
    database: Path
    observation_id: str


@dataclass(frozen=True, slots=True)
class _FinalizedTemplate:
    database: Path
    observation_ids: tuple[str, str]


@pytest.fixture(scope="module")
def native_template(tmp_path_factory: pytest.TempPathFactory) -> _NativeTemplate:
    root = tmp_path_factory.mktemp("mp05-refund-origin-native")
    source = prepare_v3_source(root)
    writer = open_v3_writer(source)
    try:
        history = add_rich_cutover_history(writer)
        second_payer = create_funded_payer(writer, name="Second historical refund")
        second = lock_escrow(
            writer,
            payer_id=second_payer,
            task_id=f"t-{uuid4()}",
            amount=85,
        )
        second_operation_id = operation_id_for(
            "task-settlement",
            (second.task_id, "posting_compensation"),
        )
        release_escrow(
            writer,
            second,
            recipient_id=second.payer_id,
            operation_id=second_operation_id,
            source_reference=f"task:{second.task_id}:posting_compensation",
        )
    finally:
        writer.close()

    _migrate(source, backup_id="refund-origin-native-before-mp05")
    existing = (
        _observation_id(source.database, history.refunded_orphan.escrow_id),
        _observation_id(source.database, second.escrow_id),
    )
    pending = tuple(
        _observation_id(source.database, lock.escrow_id) for lock in history.pending_orphans
    )
    assert len(pending) == 2
    return _NativeTemplate(source.database, source.caller_kid, existing, pending)


@pytest.fixture(scope="module")
def legacy_template(tmp_path_factory: pytest.TempPathFactory) -> _LegacyTemplate:
    root = tmp_path_factory.mktemp("mp05-refund-origin-legacy")
    source = prepare_v3_source_with_legacy_refund(root)
    _migrate(source, backup_id="refund-origin-legacy-before-mp05")
    return _LegacyTemplate(
        source.database,
        _observation_id(source.database, LEGACY_REFUNDED_ESCROW_ID),
    )


@pytest.fixture(scope="module")
def finalized_template(
    native_template: _NativeTemplate,
    tmp_path_factory: pytest.TempPathFactory,
) -> _FinalizedTemplate:
    root = tmp_path_factory.mktemp("mp05-refund-origin-finalized")
    database = _copy_database(native_template.database, root / "finalized.db")
    writer = DbWriter(str(database), 5000, "wal", None)
    try:
        for observation_id in native_template.pending_observation_ids:
            _persist_exact_refund(writer, observation_id)
            _finalize_from_authoritative_receipt(writer.connection, observation_id)
    finally:
        writer.close()
    _assert_audit_and_startup_accept(database)
    return _FinalizedTemplate(database, native_template.pending_observation_ids)


def test_legacy_existing_refund_cannot_be_relabelled_as_pending(
    legacy_template: _LegacyTemplate,
    tmp_path: Path,
) -> None:
    database = _copy_database(legacy_template.database, tmp_path / "legacy-relabel.db")
    before = _immutable_observation(database, legacy_template.observation_id)
    assert before["fact"]["refund_origin"] == "existing_refund"
    assert before["fact"]["existing_refund_provenance"]["kind"] == "legacy"
    assert before["refund_operation_id"] is None

    _relabel_existing_refund_as_pending(database, legacy_template.observation_id)

    after = _immutable_observation(database, legacy_template.observation_id)
    assert after["observed_fact_json"] == before["observed_fact_json"]
    assert after["observed_fact_sha256"] == before["observed_fact_sha256"]
    _assert_audit_and_startup_reject(database)


def test_native_existing_refund_cannot_be_relabelled_as_pending(
    native_template: _NativeTemplate,
    tmp_path: Path,
) -> None:
    observation_id = native_template.existing_observation_ids[0]
    database = _copy_database(native_template.database, tmp_path / "native-relabel.db")
    before = _immutable_observation(database, observation_id)
    provenance = before["fact"]["existing_refund_provenance"]
    assert provenance["kind"] == "native"
    assert _receipt_kind(database, provenance["operation_id"]) == "escrow_release"

    _relabel_existing_refund_as_pending(database, observation_id)

    after = _immutable_observation(database, observation_id)
    assert after["observed_fact_json"] == before["observed_fact_json"]
    assert after["observed_fact_sha256"] == before["observed_fact_sha256"]
    _assert_audit_and_startup_reject(database)


def test_native_existing_refund_completion_cannot_use_another_authoritative_receipt(
    native_template: _NativeTemplate,
    tmp_path: Path,
) -> None:
    target_id, donor_id = native_template.existing_observation_ids
    database = _copy_database(native_template.database, tmp_path / "native-substitution.db")
    before = _immutable_observation(database, target_id)
    original_operation_id = before["fact"]["existing_refund_provenance"]["operation_id"]

    donor = _completion(database, donor_id)
    assert donor["refund_operation_id"] != original_operation_id
    assert _receipt_kind(database, donor["refund_operation_id"]) == "escrow_release"
    _replace_completion(database, target_id, donor)

    after = _immutable_observation(database, target_id)
    assert after["observed_fact_json"] == before["observed_fact_json"]
    assert after["observed_fact_sha256"] == before["observed_fact_sha256"]
    assert after["refund_operation_id"] == donor["refund_operation_id"]
    _assert_audit_and_startup_reject(database)


def test_new_refund_completion_cannot_replace_its_deterministic_intent(
    finalized_template: _FinalizedTemplate,
    tmp_path: Path,
) -> None:
    target_id, donor_id = finalized_template.observation_ids
    database = _copy_database(finalized_template.database, tmp_path / "new-substitution.db")
    before = _immutable_observation(database, target_id)
    assert before["fact"]["refund_origin"] == "new_refund"
    deterministic_operation_id = before["refund_operation_id"]

    donor = _completion(database, donor_id)
    assert donor["refund_operation_id"] != deterministic_operation_id
    assert _receipt_kind(database, donor["refund_operation_id"]) == "escrow_release"
    _replace_completion(database, target_id, donor)

    after = _immutable_observation(database, target_id)
    assert after["observed_fact_json"] == before["observed_fact_json"]
    assert after["observed_fact_sha256"] == before["observed_fact_sha256"]
    assert after["refund_operation_id"] == donor["refund_operation_id"]
    _assert_audit_and_startup_reject(database)


def test_untouched_legacy_existing_refund_remains_admissible(
    legacy_template: _LegacyTemplate,
    tmp_path: Path,
) -> None:
    database = _copy_database(legacy_template.database, tmp_path / "legacy-control.db")
    _assert_audit_and_startup_accept(database)


def test_untouched_native_existing_refunds_remain_admissible(
    native_template: _NativeTemplate,
    tmp_path: Path,
) -> None:
    database = _copy_database(native_template.database, tmp_path / "native-control.db")
    _assert_audit_and_startup_accept(database)


def test_correctly_finalized_new_refunds_remain_admissible(
    finalized_template: _FinalizedTemplate,
    tmp_path: Path,
) -> None:
    database = _copy_database(finalized_template.database, tmp_path / "finalized-control.db")
    _assert_audit_and_startup_accept(database)


def _migrate(source: V3Source, *, backup_id: str) -> None:
    publish_verified_backup(source, backup_id=backup_id)
    result = migrate_v3_to_v4(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
    )
    assert result.observation == "committed_v4_verified"


def _persist_exact_refund(writer: DbWriter, observation_id: str) -> None:
    row = _row(writer.connection, observation_id)
    request = json.loads(str(row["refund_request_json"]))
    result = writer.escrow_release(
        {
            "operation_id": row["refund_operation_id"],
            "escrow_id": row["escrow_id"],
            "recipient_account_id": row["poster_id"],
            "source_reference": request["source_reference"],
            "tx_id": f"tx-{uuid4()}",
            "resolved_at": _RESOLVED_AT,
            "event": {
                "event_source": "central-bank",
                "event_type": "escrow.released",
                "timestamp": _RESOLVED_AT,
                "task_id": row["task_id"],
                "agent_id": row["poster_id"],
                "summary": "MP-05 exact posting refund",
                "payload": "{}",
            },
        }
    )
    assert result["receipt"]["operation_id"] == row["refund_operation_id"]


def _finalize_from_authoritative_receipt(
    connection: sqlite3.Connection,
    observation_id: str,
) -> None:
    row = _row(connection, observation_id)
    receipt = connection.execute(
        "SELECT canonical_request_json,request_sha256,event_id,committed_at,result_json "
        "FROM bank_operation_receipts WHERE operation_id=?",
        (row["refund_operation_id"],),
    ).fetchone()
    assert receipt is not None
    assert (row["refund_request_json"], row["refund_request_sha256"]) == tuple(receipt[:2])
    connection.execute(
        "UPDATE board_legacy_posting_observations SET state='refunded',"
        "refund_receipt_event_id=?,refunded_at_us=?,result_json=? "
        "WHERE observation_id=?",
        (
            receipt[2],
            parse_utc_text_to_epoch_us(str(receipt[3])),
            receipt[4],
            observation_id,
        ),
    )
    connection.commit()


def _relabel_existing_refund_as_pending(database: Path, observation_id: str) -> None:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        row = _row(connection, observation_id)
        operation_id = operation_id_for("task-posting-refund", (str(row["escrow_id"]),))
        request = canonical_request(
            "escrow_release",
            {
                "escrow_id": row["escrow_id"],
                "recipient_account_id": row["poster_id"],
                "source_reference": (f"task:{row['task_id']}:posting_refund:{row['escrow_id']}"),
            },
        )
        connection.execute(
            "UPDATE board_legacy_posting_observations SET state='refund_pending',"
            "refund_operation_id=?,refund_request_json=?,refund_request_sha256=?,"
            "refund_receipt_event_id=NULL,refunded_at_us=NULL,result_json=NULL,error_json=NULL "
            "WHERE observation_id=?",
            (operation_id, request.canonical_json, request.request_sha256, observation_id),
        )
        connection.commit()
    finally:
        connection.close()


def _replace_completion(
    database: Path,
    observation_id: str,
    donor: dict[str, object],
) -> None:
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE board_legacy_posting_observations SET refund_operation_id=?,"
            "refund_request_json=?,refund_request_sha256=?,refund_receipt_event_id=?,"
            "refunded_at_us=?,result_json=? WHERE observation_id=?",
            (
                donor["refund_operation_id"],
                donor["refund_request_json"],
                donor["refund_request_sha256"],
                donor["refund_receipt_event_id"],
                donor["refunded_at_us"],
                donor["result_json"],
                observation_id,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _completion(database: Path, observation_id: str) -> dict[str, object]:
    connection = _open_immutable(database)
    connection.row_factory = sqlite3.Row
    try:
        row = _row(connection, observation_id)
        return {
            name: row[name]
            for name in (
                "refund_operation_id",
                "refund_request_json",
                "refund_request_sha256",
                "refund_receipt_event_id",
                "refunded_at_us",
                "result_json",
            )
        }
    finally:
        connection.close()


def _immutable_observation(database: Path, observation_id: str) -> dict[str, object]:
    connection = _open_immutable(database)
    connection.row_factory = sqlite3.Row
    try:
        row = _row(connection, observation_id)
        return {
            "observed_fact_json": row["observed_fact_json"],
            "observed_fact_sha256": row["observed_fact_sha256"],
            "refund_operation_id": row["refund_operation_id"],
            "fact": json.loads(str(row["observed_fact_json"])),
        }
    finally:
        connection.close()


def _receipt_kind(database: Path, operation_id: object) -> str:
    assert isinstance(operation_id, str)
    connection = _open_immutable(database)
    try:
        row = connection.execute(
            "SELECT operation_kind FROM bank_operation_receipts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def _observation_id(database: Path, escrow_id: str) -> str:
    connection = _open_immutable(database)
    try:
        row = connection.execute(
            "SELECT observation_id FROM board_legacy_posting_observations WHERE escrow_id=?",
            (escrow_id,),
        ).fetchone()
        assert row is not None
        return str(row[0])
    finally:
        connection.close()


def _row(connection: sqlite3.Connection, observation_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM board_legacy_posting_observations WHERE observation_id=?",
        (observation_id,),
    ).fetchone()
    assert isinstance(row, sqlite3.Row)
    return row


def _assert_audit_and_startup_reject(database: Path) -> None:
    connection = _open_immutable(database)
    try:
        audit_rejected = not audit_publications(connection).publications_clean
    finally:
        connection.close()

    writer: DbWriter | None = None
    startup_error: ServiceError | None = None
    try:
        writer = DbWriter(str(database), 5000, "wal", None)
    except ServiceError as exc:
        startup_error = exc
    finally:
        if writer is not None:
            writer.close()
    assert audit_rejected, "Publication audit accepted a refund-origin corruption"
    assert startup_error is not None
    assert startup_error.error == "database_publication_contract_failed"


def _assert_audit_and_startup_accept(database: Path) -> None:
    connection = _open_immutable(database)
    try:
        result = audit_publications(connection)
        assert result.publications_clean, result.findings
    finally:
        connection.close()
    writer = DbWriter(str(database), 5000, "wal", None)
    writer.close()


def _copy_database(source: Path, target: Path) -> Path:
    _assert_standalone(source)
    shutil.copy2(source, target)
    return target


def _open_immutable(database: Path) -> sqlite3.Connection:
    _assert_standalone(database)
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _assert_standalone(database: Path) -> None:
    wal = Path(f"{database}-wal")
    assert not wal.exists() or wal.stat().st_size == 0

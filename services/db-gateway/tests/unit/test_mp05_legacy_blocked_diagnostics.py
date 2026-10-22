"""Regression proofs for exact MP-05 legacy refund block diagnostics."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.money_audit import audit_money
from db_gateway_service.publication_audit import audit_publications
from db_gateway_service.receipt_audit import audit_receipt_ownership
from db_gateway_service.services.db_writer import DbWriter
from tests.unit.test_mp05_legacy_posting_recovery import (
    _migrated_pending_source,
    _persist_conflicting_refund,
    _reconcile,
)

pytestmark = pytest.mark.unit

_ERROR = "posting_refund_integrity_blocked"
_MESSAGE = "Posting refund evidence requires reconciliation"
_VALID_REASONS = {
    "observation_invalid",
    "receipt_invalid",
    "escrow_invalid",
    "settlement_conflict",
    "task_escrow_reference_conflict",
}


@dataclass(frozen=True, slots=True)
class _BlockedTemplate:
    database: Path
    observation_id: str


@pytest.fixture(scope="module")
def blocked_template(tmp_path_factory: pytest.TempPathFactory) -> _BlockedTemplate:
    root = tmp_path_factory.mktemp("mp05-legacy-block-diagnostic")
    source, history = _migrated_pending_source(root)
    observation_id = _observation_id(source.database, history.pending_orphans[0].escrow_id)
    writer = DbWriter(str(source.database), 5000, "wal", None)
    try:
        _persist_conflicting_refund(writer, observation_id, "foreign_party")
        assert audit_money(writer.connection).financially_clean
        assert audit_receipt_ownership(writer.connection).ownership_clean
        with pytest.raises(ServiceError) as raised:
            _reconcile(writer, observation_id, source.caller_kid)
        assert raised.value.status_code == 409
        assert raised.value.error == _ERROR
        assert raised.value.message == _MESSAGE
        assert set(raised.value.details) == {"observation"}
        envelope = raised.value.details["observation"]
        assert envelope["observation_state"] == "blocked_integrity"
        assert set(envelope["error"]) == {"error", "message", "details"}
        assert envelope["error"]["error"] == _ERROR
        assert envelope["error"]["message"] == _MESSAGE
        assert set(envelope["error"]["details"]) == {"reason"}
        assert envelope["error"]["details"]["reason"] in _VALID_REASONS
        stored = writer.connection.execute(
            "SELECT error_json FROM board_legacy_posting_observations WHERE observation_id=?",
            (observation_id,),
        ).fetchone()
        assert stored is not None
        assert stored[0] == _canonical_json(envelope["error"])
    finally:
        writer.close()
    _assert_standalone(source.database)
    return _BlockedTemplate(source.database, observation_id)


def test_well_formed_blocked_diagnostic_survives_audit_and_restart(
    blocked_template: _BlockedTemplate,
    tmp_path: Path,
) -> None:
    database = _copy_database(blocked_template.database, tmp_path / "valid-block.db")
    _assert_audit_and_startup_accept(database)


@pytest.mark.parametrize(
    ("case", "malformed"),
    (
        (
            "wrong-code",
            {
                "error": "publication_integrity_blocked",
                "message": _MESSAGE,
                "details": {"reason": "settlement_conflict"},
            },
        ),
        (
            "wrong-message",
            {
                "error": _ERROR,
                "message": "Posting refund is blocked",
                "details": {"reason": "settlement_conflict"},
            },
        ),
        (
            "details-extra",
            {
                "error": _ERROR,
                "message": _MESSAGE,
                "details": {"reason": "settlement_conflict", "operation_id": "private"},
            },
        ),
        (
            "details-missing",
            {
                "error": _ERROR,
                "message": _MESSAGE,
            },
        ),
        (
            "invalid-reason",
            {
                "error": _ERROR,
                "message": _MESSAGE,
                "details": {"reason": "publication_conflict"},
            },
        ),
        (
            "reason-type",
            {
                "error": _ERROR,
                "message": _MESSAGE,
                "details": {"reason": 1},
            },
        ),
    ),
)
def test_malformed_blocked_diagnostic_is_rejected_by_audit_and_restart(
    blocked_template: _BlockedTemplate,
    tmp_path: Path,
    case: str,
    malformed: dict[str, object],
) -> None:
    database = _copy_database(blocked_template.database, tmp_path / f"{case}.db")
    before = _bank_snapshot(database)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE board_legacy_posting_observations SET error_json=? WHERE observation_id=?",
            (_canonical_json(malformed), blocked_template.observation_id),
        )
        connection.commit()
    finally:
        connection.close()
    assert _bank_snapshot(database) == before
    _assert_audit_and_startup_reject(database)


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
    assert audit_rejected, "Publication audit accepted a malformed legacy block diagnostic"
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


def _bank_snapshot(database: Path) -> dict[str, list[tuple[object, ...]]]:
    connection = _open_immutable(database)
    try:
        return {
            table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in (
                "bank_accounts",
                "bank_transactions",
                "bank_escrow",
                "bank_operation_receipts",
                "bank_operation_legs",
            )
        }
    finally:
        connection.close()


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


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )

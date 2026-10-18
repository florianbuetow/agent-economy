"""The running Gateway must use v3 and reject older or incomplete task state."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.migration import migrate_v2_to_v3
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_contract_audit import audit_task_contracts
from tests.unit.test_mp07_migration_contract import _prepared_v2

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _dump(database: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return tuple(connection.iterdump())
    finally:
        connection.close()


def test_gateway_bootstraps_the_explicit_v3_catalog(tmp_path: Path) -> None:
    writer = DbWriter(
        db_path=str(tmp_path / "fresh.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V3),
    )
    try:
        assert writer.connection.execute("SELECT version FROM schema_metadata").fetchone()[0] == 3
        assert audit_task_contracts(writer.connection).contracts_clean
    finally:
        writer.close()


def test_existing_exact_v2_requires_migration_without_startup_mutation(tmp_path: Path) -> None:
    database = tmp_path / "old.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V2))
    finally:
        connection.close()
    before = _dump(database)
    writer: DbWriter | None = None
    try:
        with pytest.raises(ServiceError) as error:
            writer = DbWriter(str(database), 5000, "wal", None)
        assert error.value.error == "migration_required"
        assert error.value.status_code == 503
        assert error.value.details["version"] == 2
        assert _dump(database) == before
    finally:
        if writer is not None:
            writer.close()


def test_v3_startup_rejects_a_task_missing_its_contract_sidecar(tmp_path: Path) -> None:
    inventory, backups, work, database = _prepared_v2(tmp_path)
    result = migrate_v2_to_v3(
        inventory,
        backup_root=backups,
        backup_id="before-mp07",
        work_root=work,
        busy_timeout_ms=5000,
    )
    assert result.observation == "committed_v3_verified"
    connection = sqlite3.connect(database)
    try:
        connection.execute("DELETE FROM board_task_contracts WHERE task_id='t-1'")
        connection.commit()
    finally:
        connection.close()
    before = _dump(database)
    with pytest.raises(ServiceError) as error:
        DbWriter(str(database), 5000, "wal", None)
    assert error.value.error == "database_task_contract_failed"
    assert "missing_task_contract" in error.value.details["finding_codes"]
    assert _dump(database) == before

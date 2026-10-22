"""The running Gateway must use v4 and reject older or incomplete task state."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service import migration
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_contract_audit import audit_task_contracts
from tests.fakes.mp05_publication_migration_history import (
    prepare_v3_source,
    publish_verified_backup,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _dump(database: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return tuple(connection.iterdump())
    finally:
        connection.close()


def test_gateway_bootstraps_the_explicit_v4_catalog(tmp_path: Path) -> None:
    writer = DbWriter(
        db_path=str(tmp_path / "fresh.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V4),
    )
    try:
        assert writer.connection.execute("SELECT version FROM schema_metadata").fetchone()[0] == 4
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


def test_v4_startup_rejects_a_task_missing_its_contract_sidecar(tmp_path: Path) -> None:
    source = prepare_v3_source(tmp_path)
    backup_id = "before-mp05-runtime-sidecar"
    publish_verified_backup(source, backup_id=backup_id)
    result = migration.migrate_v3_to_v4(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
        busy_timeout_ms=5000,
    )
    assert result.observation == "committed_v4_verified"
    connection = sqlite3.connect(source.database)
    try:
        connection.execute("DELETE FROM board_task_contracts WHERE task_id='t-1'")
        connection.commit()
    finally:
        connection.close()
    before = _dump(source.database)
    with pytest.raises(ServiceError) as error:
        DbWriter(str(source.database), 5000, "wal", None)
    assert error.value.error == "database_task_contract_failed"
    assert "missing_task_contract" in error.value.details["finding_codes"]
    assert _dump(source.database) == before

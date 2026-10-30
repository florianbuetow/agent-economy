"""The running Gateway must use V6 and reject older schema versions."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from db_gateway_service.task_contract_audit import audit_task_contracts

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _dump(database: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        return tuple(connection.iterdump())
    finally:
        connection.close()


def test_gateway_bootstraps_the_explicit_v6_catalog(tmp_path: Path) -> None:
    writer = DbWriter(
        db_path=str(tmp_path / "fresh.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
        current_task_board_caller_kid="gateway-task-board",
    )
    try:
        assert writer.connection.execute("SELECT version FROM schema_metadata").fetchone()[0] == 6
        assert audit_task_contracts(writer.connection).contracts_clean
    finally:
        writer.close()


def test_existing_exact_v2_requires_reset_without_startup_mutation(tmp_path: Path) -> None:
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
            writer = DbWriter(
                str(database),
                5000,
                "wal",
                None,
                current_task_board_caller_kid="gateway-task-board",
            )
        assert error.value.error == "database_reset_required"
        assert error.value.status_code == 503
        assert error.value.details["version"] == 2
        assert _dump(database) == before
    finally:
        if writer is not None:
            writer.close()

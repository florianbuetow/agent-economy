"""MP-04 startup admission for current schema and unchanged v2 receipt ownership."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from tests.fakes.mp05_publication_migration_history import prepare_v3_source

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _write_schema(path: Path, state: SchemaState) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(load_schema_sql(state))
    finally:
        connection.close()


def test_existing_verified_v3_requires_explicit_migration_without_startup_mutation(
    tmp_path: Path,
) -> None:
    source = prepare_v3_source(tmp_path)
    before = source.database.read_bytes()

    with pytest.raises(ServiceError) as captured:
        DbWriter(
            str(source.database),
            5000,
            "wal",
            None,
            current_task_board_caller_kid="gateway-task-board",
        )

    assert captured.value.status_code == 503
    assert captured.value.error == "database_reset_required"
    assert captured.value.details["version"] == 3
    assert source.database.read_bytes() == before


@pytest.mark.parametrize("state", [SchemaState.CURRENT_V1, SchemaState.CURRENT_V2])
def test_older_schema_requires_explicit_migration_before_persistent_configuration(
    tmp_path: Path,
    state: SchemaState,
) -> None:
    path = tmp_path / f"{state.value}.db"
    _write_schema(path, state)
    before = path.read_bytes()
    with pytest.raises(ServiceError) as captured:
        DbWriter(
            str(path),
            5000,
            "wal",
            None,
            current_task_board_caller_kid="gateway-task-board",
        )

    assert captured.value.error == "database_reset_required"
    assert path.read_bytes() == before


def test_empty_database_bootstraps_exact_v6_transactionally(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    writer = DbWriter(
        str(path),
        5000,
        "wal",
        load_schema_sql(SchemaState.CURRENT_V6),
        current_task_board_caller_kid="gateway-task-board",
    )
    try:
        assert classify_schema(writer.connection).state is SchemaState.CURRENT_V6
        assert (
            writer.connection.execute("SELECT cutover_kind FROM bank_receipt_cutover").fetchone()[0]
            == "fresh_v2"
        )
    finally:
        writer.close()


def test_tampered_current_bootstrap_rolls_back_every_schema_object(tmp_path: Path) -> None:
    path = tmp_path / "tampered-bootstrap.db"
    schema = load_schema_sql(SchemaState.CURRENT_V6).replace(
        "a81b1a47e8cd319a73362c83a992f181986e799b1e82753bf139e60b35d0a4f6",
        "f" * 64,
        1,
    )
    with pytest.raises((ServiceError, sqlite3.Error)):
        DbWriter(
            str(path),
            5000,
            "wal",
            schema,
            current_task_board_caller_kid="gateway-task-board",
        )

    connection = sqlite3.connect(path)
    try:
        objects = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert objects == []
    finally:
        connection.close()


@pytest.mark.parametrize("tamper", ["missing-cutover", "unowned-transaction"])
def test_receipt_ownership_failure_is_rejected_before_configuration(
    tmp_path: Path,
    tamper: str,
) -> None:
    path = tmp_path / f"{tamper}.db"
    _write_schema(path, SchemaState.CURRENT_V6)
    connection = sqlite3.connect(path)
    if tamper == "missing-cutover":
        connection.execute("DELETE FROM bank_receipt_cutover")
    else:
        connection.execute("INSERT INTO identity_agents VALUES ('a-1','A','key','now')")
        connection.execute("INSERT INTO bank_accounts VALUES ('a-1',10,'now')")
        connection.execute(
            "INSERT INTO bank_transactions "
            "(tx_id,account_id,type,amount,balance_after,reference,timestamp) "
            "VALUES ('tx-1','a-1','credit',10,10,'seed','now')"
        )
    connection.commit()
    connection.close()
    with pytest.raises(ServiceError) as captured:
        DbWriter(
            str(path),
            5000,
            "wal",
            None,
            current_task_board_caller_kid="gateway-task-board",
        )

    assert captured.value.error == "receipt_ownership_failed"

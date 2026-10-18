"""MP-04 startup admission for exact schema v2 and receipt ownership."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _write_schema(path: Path, state: SchemaState) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(load_schema_sql(state))
    finally:
        connection.close()


def test_existing_exact_v2_is_the_only_admitted_runtime_schema(tmp_path: Path) -> None:
    path = tmp_path / "v2.db"
    _write_schema(path, SchemaState.CURRENT_V2)
    writer = DbWriter(str(path), 5000, "wal", None)
    try:
        assert classify_schema(writer.connection).state is SchemaState.CURRENT_V2
    finally:
        writer.close()


def test_existing_v1_requires_explicit_migration_before_persistent_configuration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "v1.db"
    _write_schema(path, SchemaState.CURRENT_V1)
    before = path.read_bytes()
    with pytest.raises(ServiceError) as captured:
        DbWriter(str(path), 5000, "wal", None)

    assert captured.value.error == "migration_required"
    assert path.read_bytes() == before


def test_empty_database_bootstraps_exact_v2_transactionally(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    writer = DbWriter(
        str(path),
        5000,
        "wal",
        load_schema_sql(SchemaState.CURRENT_V2),
    )
    try:
        assert classify_schema(writer.connection).state is SchemaState.CURRENT_V2
        assert (
            writer.connection.execute("SELECT cutover_kind FROM bank_receipt_cutover").fetchone()[0]
            == "fresh_v2"
        )
    finally:
        writer.close()


def test_tampered_v2_bootstrap_rolls_back_every_schema_object(tmp_path: Path) -> None:
    path = tmp_path / "tampered-bootstrap.db"
    schema = load_schema_sql(SchemaState.CURRENT_V2).replace(
        "a81b1a47e8cd319a73362c83a992f181986e799b1e82753bf139e60b35d0a4f6",
        "f" * 64,
        1,
    )
    with pytest.raises((ServiceError, sqlite3.Error)):
        DbWriter(str(path), 5000, "wal", schema)

    connection = sqlite3.connect(path)
    try:
        objects = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert objects == []
    finally:
        connection.close()


@pytest.mark.parametrize("tamper", ["missing-cutover", "unowned-transaction"])
def test_v2_receipt_ownership_failure_is_rejected_before_configuration(
    tmp_path: Path,
    tamper: str,
) -> None:
    path = tmp_path / f"{tamper}.db"
    _write_schema(path, SchemaState.CURRENT_V2)
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
        DbWriter(str(path), 5000, "wal", None)

    assert captured.value.error == "receipt_ownership_failed"

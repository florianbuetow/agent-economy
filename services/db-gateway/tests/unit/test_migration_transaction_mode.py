"""A migration persists when the DB-API leaves transaction control to SQL."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from db_gateway_service import migration
from db_gateway_service.schema import SchemaState, classify_schema
from tests.unit.test_mp06_migration import _prepared_v0

pytestmark = pytest.mark.unit


def test_driver_methods_are_noops_but_native_close_rolls_back_explicit_transaction(tmp_path):
    database = tmp_path / "driver-control.db"
    connection = sqlite3.connect(
        database,
        factory=sqlite3.Connection,
        check_same_thread=False,
        cached_statements=0,
        autocommit=True,
    )
    try:
        connection.execute("CREATE TABLE proof (value INTEGER)").close()
        connection.execute("BEGIN IMMEDIATE").close()
        connection.execute("INSERT INTO proof VALUES (1)").close()
        connection.commit()
        connection.rollback()
        assert connection.in_transaction
    finally:
        sqlite3.Connection.close(connection)
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as observed:
        assert observed.execute("SELECT * FROM proof").fetchall() == []


def test_explicit_sql_transaction_persists_in_sqlite_autocommit_mode(tmp_path, monkeypatch):
    inventory, backup_root, work_root, database = _prepared_v0(tmp_path)
    transactions = []

    def open_writer(path, busy_timeout_ms):
        connection = sqlite3.connect(
            path.as_uri() + "?mode=rw",
            uri=True,
            factory=sqlite3.Connection,
            check_same_thread=False,
            cached_statements=0,
            autocommit=True,
            timeout=busy_timeout_ms / 1000,
        )
        assert not connection.in_transaction

        def trace(statement):
            if statement in {"BEGIN IMMEDIATE", "COMMIT", "ROLLBACK"}:
                transactions.append(statement)

        connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(migration, "_open_live_writer", open_writer)
    result = migration.migrate_v0_to_v1(
        inventory,
        backup_root=backup_root,
        backup_id="before-mp06",
        work_root=work_root,
        busy_timeout_ms=5000,
    )
    assert result.applied is True
    assert result.observation == "committed_v1_verified"
    assert transactions == ["BEGIN IMMEDIATE", "COMMIT"]
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        assert classify_schema(connection).state is SchemaState.CURRENT_V1

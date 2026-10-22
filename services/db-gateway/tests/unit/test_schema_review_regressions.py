"""Schema review regressions must reject changed semantics and preserve atomicity."""

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


def test_current_schema_requires_its_version_row() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        connection.execute("DELETE FROM schema_metadata")
        assert classify_schema(connection).state is not SchemaState.CURRENT_V1
    finally:
        connection.close()


def test_future_version_in_exact_current_table_shape_is_not_admitted() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V4))
        assert classify_schema(connection).state is SchemaState.CURRENT_V4
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE schema_metadata SET version=5")
        connection.execute("PRAGMA ignore_check_constraints=OFF")
        assert classify_schema(connection).state is SchemaState.FUTURE
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("original", "changed"),
    [
        ("typeof(balance) = 'integer'", "typeof(balance) = 'INTEGER'"),
        ("DEFAULT 'locked'", "DEFAULT 'LOCKED'"),
        (
            "account_id     TEXT PRIMARY KEY,",
            "account_id     TEXT PRIMARY KEY ON CONFLICT REPLACE,",
        ),
        (
            "FOREIGN KEY (account_id) REFERENCES identity_agents (agent_id)",
            "FOREIGN KEY (account_id) REFERENCES identity_agents (agent_id) "
            "DEFERRABLE INITIALLY DEFERRED",
        ),
    ],
)
def test_sql_semantic_changes_do_not_match_current_schema(original: str, changed: str) -> None:
    baseline = load_schema_sql(SchemaState.CURRENT_V1)
    assert original in baseline
    candidate = baseline.replace(original, changed, 1)
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(candidate)
        assert classify_schema(connection).state is not SchemaState.CURRENT_V1
    finally:
        connection.close()


def test_commented_out_money_check_is_not_a_real_constraint() -> None:
    check = "CHECK (typeof(balance) = 'integer' AND balance BETWEEN 0 AND 9223372036854775807)"
    baseline = load_schema_sql(SchemaState.CURRENT_V1)
    assert check in baseline
    candidate = baseline.replace(check, f"/* {check} */", 1)
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(candidate)
        connection.execute(
            "INSERT INTO bank_accounts (account_id, balance, created_at) VALUES ('a', -1, 'now')"
        )
        assert connection.execute("SELECT balance FROM bank_accounts").fetchone() == (-1,)
        assert classify_schema(connection).state is not SchemaState.CURRENT_V1
    finally:
        connection.close()


def test_commented_transaction_control_cannot_escape_bootstrap_rollback(tmp_path: Path) -> None:
    path = tmp_path / "bootstrap.db"
    candidate = (
        "/* This must not commit the bootstrap transaction. */ COMMIT;\n"
        + load_schema_sql(SchemaState.CURRENT_V1)
        + "\nSELECT unavailable FROM missing_table;\n"
    )
    with pytest.raises((sqlite3.DatabaseError, ServiceError)):
        DbWriter(str(path), busy_timeout_ms=5000, journal_mode="wal", schema_sql=candidate)
    connection = sqlite3.connect(path)
    try:
        objects = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        assert objects == []
    finally:
        connection.close()


def test_failed_admission_keeps_primary_error_when_close_also_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unknown.db"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE unrelated (value TEXT)")
    connection.close()
    original_connect = sqlite3.connect

    class FailingCloseConnection(sqlite3.Connection):
        def close(self) -> None:
            super().close()
            raise RuntimeError("secondary close failure")

    def connect(database: str, **kwargs: object) -> sqlite3.Connection:
        if str(database) == str(path):
            return original_connect(database, factory=FailingCloseConnection, **kwargs)
        return original_connect(database, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(ServiceError, match="incomplete or unknown"):
        DbWriter(str(path), busy_timeout_ms=5000, journal_mode="wal", schema_sql=None)

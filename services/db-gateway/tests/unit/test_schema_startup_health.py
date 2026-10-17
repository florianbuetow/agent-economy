"""Startup rejects structurally current databases with corrupt stored data."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

import db_gateway_service.services.db_writer as db_writer_module
from db_gateway_service.schema import SchemaState, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


pytestmark = pytest.mark.unit

BUSY_TIMEOUT_MS = 5000
JOURNAL_MODE = "wal"


def _create_current_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        connection.commit()
    finally:
        connection.close()


def _insert_orphan_account(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) "
        "VALUES ('orphan-account', 1, '2026-10-16T00:00:00Z')"
    )


def _insert_invalid_balance(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES ('agent-invalid', 'Invalid', 'ed25519:test-invalid', "
        "'2026-10-16T00:00:00Z')"
    )
    connection.execute("PRAGMA ignore_check_constraints=ON")
    connection.execute(
        "INSERT INTO bank_accounts (account_id, balance, created_at) "
        "VALUES ('agent-invalid', 'invalid-balance', '2026-10-16T00:00:00Z')"
    )
    connection.execute("PRAGMA ignore_check_constraints=OFF")


def _insert_both_corruptions(connection: sqlite3.Connection) -> None:
    _insert_orphan_account(connection)
    _insert_invalid_balance(connection)


@pytest.mark.parametrize(
    (
        "corrupt",
        "expected_failed_checks",
        "expected_integrity_issues",
        "expected_foreign_key_issues",
    ),
    [
        (_insert_orphan_account, ("foreign_key_check",), 0, 1),
        (_insert_invalid_balance, ("integrity_check",), 1, 0),
        (
            _insert_both_corruptions,
            ("integrity_check", "foreign_key_check"),
            1,
            1,
        ),
    ],
    ids=["foreign-key-orphan", "check-constraint-violation", "both"],
)
def test_existing_current_schema_rejects_corrupt_data_before_journal_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corrupt: Callable[[sqlite3.Connection], None],
    expected_failed_checks: tuple[str, ...],
    expected_integrity_issues: int,
    expected_foreign_key_issues: int,
) -> None:
    path = tmp_path / f"{'-'.join(expected_failed_checks)}.db"
    _create_current_database(path)
    setup = sqlite3.connect(path)
    try:
        corrupt(setup)
        setup.commit()
        expected_rows = setup.execute(
            "SELECT account_id, balance, typeof(balance) FROM bank_accounts ORDER BY account_id"
        ).fetchall()
    finally:
        setup.close()
    before = path.read_bytes()

    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = sqlite3.Connection
        connection = real_connect(*args, **kwargs)
        if args and str(args[0]) == str(path):
            opened.append(connection)
        return connection

    monkeypatch.setattr(db_writer_module.sqlite3, "connect", tracking_connect)

    with pytest.raises(ServiceError) as raised:
        DbWriter(
            db_path=str(path),
            busy_timeout_ms=BUSY_TIMEOUT_MS,
            journal_mode=JOURNAL_MODE,
            schema_sql=None,
        )

    assert raised.value.error == "database_integrity_failed"
    assert raised.value.status_code == 500
    assert raised.value.details == {
        "failed_checks": list(expected_failed_checks),
        "foreign_key_issue_count": expected_foreign_key_issues,
        "integrity_issue_count": expected_integrity_issues,
    }
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    assert path.read_bytes() == before
    assert not path.with_name(f"{path.name}-wal").exists()
    assert not path.with_name(f"{path.name}-shm").exists()

    check = real_connect(path)
    try:
        assert check.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert (
            check.execute(
                "SELECT account_id, balance, typeof(balance) FROM bank_accounts ORDER BY account_id"
            ).fetchall()
            == expected_rows
        )
    finally:
        check.close()


@pytest.mark.parametrize(
    "failing_pragma",
    ["PRAGMA integrity_check", "PRAGMA foreign_key_check"],
    ids=["integrity-unavailable", "foreign-key-unavailable"],
)
def test_health_check_database_error_is_safe_and_non_mutating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_pragma: str,
) -> None:
    path = tmp_path / "health-check-unavailable.db"
    _create_current_database(path)
    before = path.read_bytes()
    real_connect = sqlite3.connect
    sentinel = "secret-health-check-sentinel"
    opened: list[sqlite3.Connection] = []

    class HealthCheckFailureConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:  # pyright: ignore[reportIncompatibleMethodOverride]
            if sql == failing_pragma:
                raise sqlite3.DatabaseError(sentinel)
            return super().execute(sql, parameters)  # type: ignore[arg-type]

    def failing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        kwargs["factory"] = HealthCheckFailureConnection
        connection = real_connect(*args, **kwargs)
        if args and str(args[0]) == str(path):
            opened.append(connection)
        return connection

    monkeypatch.setattr(db_writer_module.sqlite3, "connect", failing_connect)

    with pytest.raises(ServiceError) as raised:
        DbWriter(
            db_path=str(path),
            busy_timeout_ms=BUSY_TIMEOUT_MS,
            journal_mode=JOURNAL_MODE,
            schema_sql=None,
        )

    assert raised.value.error == "database_integrity_failed"
    assert raised.value.status_code == 500
    assert raised.value.details == {
        "failed_checks": ["health_check_unavailable"],
        "foreign_key_issue_count": None,
        "integrity_issue_count": None,
    }
    assert sentinel not in str(raised.value)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")
    assert path.read_bytes() == before
    assert not path.with_name(f"{path.name}-wal").exists()
    assert not path.with_name(f"{path.name}-shm").exists()

    check = real_connect(path)
    try:
        assert check.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert check.execute("SELECT * FROM schema_metadata").fetchall() == [
            (1, 1, None, None, None)
        ]
        assert check.execute("SELECT * FROM identity_agents").fetchall() == []
        assert check.execute("SELECT * FROM bank_accounts").fetchall() == []
    finally:
        check.close()


def test_existing_healthy_current_schema_starts(tmp_path: Path) -> None:
    path = tmp_path / "healthy-current.db"
    _create_current_database(path)

    writer = DbWriter(
        db_path=str(path),
        busy_timeout_ms=BUSY_TIMEOUT_MS,
        journal_mode=JOURNAL_MODE,
        schema_sql=None,
    )
    writer.close()


def test_empty_database_bootstraps_and_passes_health_admission(tmp_path: Path) -> None:
    writer = DbWriter(
        db_path=str(tmp_path / "healthy-bootstrap.db"),
        busy_timeout_ms=BUSY_TIMEOUT_MS,
        journal_mode=JOURNAL_MODE,
        schema_sql=load_schema_sql(SchemaState.CURRENT_V1),
    )
    try:
        integrity_row = writer.connection.execute("PRAGMA integrity_check").fetchone()
        assert integrity_row is not None
        assert integrity_row[0] == "ok"
        assert writer.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        writer.close()

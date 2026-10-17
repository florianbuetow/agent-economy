"""MP-06 exact schema classification and startup-admission tests."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from service_commons.exceptions import ServiceError

import db_gateway_service.services.db_writer as db_writer_module
from db_gateway_service.core import lifespan as lifespan_module
from db_gateway_service.schema import (
    SQLITE_MAX_INTEGER,
    SchemaState,
    classify_schema,
    load_schema_sql,
)
from db_gateway_service.services.db_writer import DbWriter

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


BUSY_TIMEOUT_MS = 5000
JOURNAL_MODE = "wal"


def _application_objects(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND type IN ('table', 'index', 'view', 'trigger') "
        "ORDER BY name"
    ).fetchall()
    return [str(row[0]) for row in rows]


def _open(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def _historical_legacy_sql() -> str:
    sql = load_schema_sql(SchemaState.SUPPORTED_V0)
    sql = re.sub(
        r"^\s*event_id\s+INTEGER,\s+-- event row emitted[^\n]+\n",
        "",
        sql,
        flags=re.MULTILINE,
    )
    for distinctive_comment in (
        "court claim id",
        "court rebuttal window close",
        "set when the worker rebuttal is forwarded",
    ):
        sql = re.sub(
            rf"^\s*[^\n]+-- [^\n]*{re.escape(distinctive_comment)}[^\n]*\n",
            "",
            sql,
            flags=re.MULTILINE,
        )
    return sql


def _legacy_schema_without_connection_pragmas() -> str:
    sql = load_schema_sql(SchemaState.SUPPORTED_V0)
    for pragma in (
        "PRAGMA journal_mode = WAL;\n",
        "PRAGMA foreign_keys = ON;\n",
        "PRAGMA busy_timeout = 5000;\n",
    ):
        sql = sql.replace(pragma, "")
    return sql


@pytest.mark.unit
def test_classifies_empty_database(tmp_path: Path) -> None:
    connection = _open(tmp_path / "empty.db")
    try:
        result = classify_schema(connection)
    finally:
        connection.close()

    assert result.state is SchemaState.EMPTY
    assert result.version is None
    assert result.findings == ()


@pytest.mark.unit
@pytest.mark.parametrize("historical_additions", [False, True])
def test_classifies_both_complete_legacy_v0_layouts(
    tmp_path: Path, *, historical_additions: bool
) -> None:
    connection = _open(tmp_path / f"legacy-{historical_additions}.db")
    try:
        if historical_additions:
            connection.executescript(_historical_legacy_sql())
            for statement in (
                "ALTER TABLE board_tasks ADD COLUMN dispute_id TEXT",
                "ALTER TABLE board_tasks ADD COLUMN rebuttal_deadline TEXT",
                "ALTER TABLE board_tasks ADD COLUMN rebuttal_submitted_at TEXT",
                "ALTER TABLE bank_transactions ADD COLUMN event_id INTEGER",
                "ALTER TABLE bank_escrow ADD COLUMN event_id INTEGER",
            ):
                connection.execute(statement)
        else:
            connection.executescript(load_schema_sql(SchemaState.SUPPORTED_V0))

        result = classify_schema(connection)
    finally:
        connection.close()

    assert result.state is SchemaState.SUPPORTED_V0
    assert result.version == 0
    assert result.findings == ()


@pytest.mark.unit
def test_classifies_exact_current_v1(tmp_path: Path) -> None:
    connection = _open(tmp_path / "current.db")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        result = classify_schema(connection)
    finally:
        connection.close()

    assert result.state is SchemaState.CURRENT_V1
    assert result.version == 1
    assert result.findings == ()


@pytest.mark.unit
def test_frozen_legacy_resource_has_expected_digest() -> None:
    digest = hashlib.sha256(load_schema_sql(SchemaState.SUPPORTED_V0).encode()).hexdigest()
    assert digest == "179e13ba9ae0425a34f2cc6dc00dced3192b8ff8c65415331256ce8ea73f52e1"


@pytest.mark.unit
def test_current_schema_records_fresh_v1_metadata(tmp_path: Path) -> None:
    connection = _open(tmp_path / "metadata.db")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        row = connection.execute(
            "SELECT singleton, version, migrated_from_version, migration_backup_id, "
            "migration_backup_manifest_sha256 FROM schema_metadata"
        ).fetchone()
    finally:
        connection.close()

    assert row == (1, 1, None, None, None)


@pytest.mark.unit
def test_current_schema_accepts_exact_migration_provenance(tmp_path: Path) -> None:
    connection = _open(tmp_path / "migrated-metadata.db")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        connection.execute(
            "UPDATE schema_metadata SET migrated_from_version = 0, "
            "migration_backup_id = 'backup-1', migration_backup_manifest_sha256 = ?",
            ("a" * 64,),
        )
        result = classify_schema(connection)
    finally:
        connection.close()

    assert result.state is SchemaState.CURRENT_V1
    assert result.version == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "balance_literal",
    ["-1", str(SQLITE_MAX_INTEGER + 1), "1.5", "'not-an-integer'"],
)
def test_current_schema_rejects_invalid_stored_balance(
    tmp_path: Path, balance_literal: str
) -> None:
    connection = _open(tmp_path / "bounded-balance.db")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        connection.execute("INSERT INTO identity_agents VALUES ('a-1', 'Alice', 'key-1', 'now')")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bank_accounts (account_id, balance, created_at) "
                f"VALUES ('a-1', {balance_literal}, 'now')"  # nosec B608
            )
    finally:
        connection.close()


@pytest.mark.unit
def test_current_schema_uses_lifetime_escrow_uniqueness(tmp_path: Path) -> None:
    connection = _open(tmp_path / "lifetime-escrow.db")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        connection.execute("INSERT INTO identity_agents VALUES ('a-1', 'Alice', 'key-1', 'now')")
        connection.execute("INSERT INTO bank_accounts VALUES ('a-1', 100, 'now')")
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
            "VALUES ('esc-1', 'a-1', 10, 't-1', 'released', 'now')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO bank_escrow "
                "(escrow_id, payer_account_id, amount, task_id, status, created_at) "
                "VALUES ('esc-2', 'a-1', 10, 't-1', 'locked', 'now')"
            )
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    "mutate",
    [
        lambda sql: sql.replace(
            " CHECK (typeof(balance) = 'integer' AND balance BETWEEN 0 AND 9223372036854775807)",
            "",
            1,
        ),
        lambda sql: sql.replace(" WITHOUT ROWID;", ";", 1),
        lambda sql: sql.replace(
            "ON bank_escrow (payer_account_id, task_id);",
            "ON bank_escrow (payer_account_id, task_id) WHERE status = 'locked';",
            1,
        ),
        lambda sql: sql.replace("name           TEXT NOT NULL,", "name           TEXT,", 1),
        lambda sql: sql.replace(
            "FOREIGN KEY (account_id) REFERENCES identity_agents (agent_id)",
            "FOREIGN KEY (account_id) REFERENCES identity_agents (name)",
            1,
        ),
    ],
    ids=["check", "table-mode", "index-predicate", "nullability", "foreign-key"],
)
def test_known_object_with_changed_structure_is_partial(
    tmp_path: Path, mutate: Callable[[str], str]
) -> None:
    connection = _open(tmp_path / "changed-current.db")
    try:
        connection.executescript(mutate(load_schema_sql(SchemaState.CURRENT_V1)))
        result = classify_schema(connection)
    finally:
        connection.close()

    assert result.state is SchemaState.PARTIAL
    assert result.findings


@pytest.mark.unit
@pytest.mark.parametrize(
    "extra_sql",
    [
        "CREATE VIEW unexpected_view AS SELECT agent_id FROM identity_agents;",
        "CREATE TRIGGER unexpected_trigger AFTER INSERT ON identity_agents BEGIN SELECT 1; END;",
    ],
    ids=["view", "trigger"],
)
def test_unexpected_view_or_trigger_is_unknown(tmp_path: Path, extra_sql: str) -> None:
    connection = _open(tmp_path / "unexpected-object.db")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1) + extra_sql)
        result = classify_schema(connection)
    finally:
        connection.close()

    assert result.state is SchemaState.UNKNOWN
    assert any(finding.startswith("unexpected:") for finding in result.findings)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("CREATE TABLE identity_agents (agent_id TEXT PRIMARY KEY)", SchemaState.PARTIAL),
        ("CREATE TABLE unrelated (value TEXT)", SchemaState.UNKNOWN),
        (
            "CREATE TABLE schema_metadata ("
            "singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL);"
            "INSERT INTO schema_metadata VALUES (1, 3)",
            SchemaState.FUTURE,
        ),
    ],
)
def test_classifies_rejected_existing_layouts(
    tmp_path: Path, sql: str, expected: SchemaState
) -> None:
    connection = _open(tmp_path / f"{expected.value}.db")
    try:
        connection.executescript(sql)
        result = classify_schema(connection)
    finally:
        connection.close()

    assert result.state is expected
    assert result.findings


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sql", "expected_error"),
    [
        (None, "schema_required"),
        (_legacy_schema_without_connection_pragmas(), "migration_required"),
        ("CREATE TABLE identity_agents (agent_id TEXT PRIMARY KEY);", "invalid_schema"),
    ],
)
def test_empty_constructor_requires_exact_current_schema_and_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sql: str | None,
    expected_error: str,
) -> None:
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(db_writer_module.sqlite3, "connect", tracking_connect)

    with pytest.raises(ServiceError) as raised:
        DbWriter(
            db_path=str(tmp_path / f"{expected_error}.db"),
            busy_timeout_ms=BUSY_TIMEOUT_MS,
            journal_mode=JOURNAL_MODE,
            schema_sql=sql,
        )

    assert raised.value.error == expected_error
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("existing_sql", "expected_error"),
    [
        (_legacy_schema_without_connection_pragmas(), "migration_required"),
        ("CREATE TABLE identity_agents (agent_id TEXT PRIMARY KEY)", "invalid_schema"),
        ("CREATE TABLE unrelated (value TEXT)", "invalid_schema"),
        (
            "CREATE TABLE schema_metadata ("
            "singleton INTEGER PRIMARY KEY, version INTEGER NOT NULL);"
            "INSERT INTO schema_metadata VALUES (1, 3)",
            "unsupported_schema_version",
        ),
    ],
)
def test_existing_rejected_schema_fails_before_persistent_journal_change(
    tmp_path: Path, existing_sql: str, expected_error: str
) -> None:
    path = tmp_path / f"{expected_error}.db"
    connection = _open(path)
    connection.executescript(existing_sql)
    connection.close()

    with pytest.raises(ServiceError) as raised:
        DbWriter(
            db_path=str(path),
            busy_timeout_ms=BUSY_TIMEOUT_MS,
            journal_mode=JOURNAL_MODE,
            schema_sql=load_schema_sql(SchemaState.CURRENT_V1),
        )

    assert raised.value.error == expected_error
    check = _open(path)
    try:
        assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        check.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    "denied",
    [
        lambda action, arg1: action == sqlite3.SQLITE_CREATE_TABLE and arg1 == "board_tasks",
        lambda action, _arg1: action == sqlite3.SQLITE_CREATE_INDEX,
        lambda action, arg1: action == sqlite3.SQLITE_INSERT and arg1 == "schema_metadata",
    ],
    ids=["table", "index", "metadata"],
)
def test_bootstrap_fault_rolls_back_all_application_objects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    denied: Callable[[int, str | None], bool],
) -> None:
    path = tmp_path / "fault.db"
    real_connect = sqlite3.connect

    def faulting_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)

        def authorize(
            action: int,
            arg1: str | None,
            _arg2: str | None,
            _database: str | None,
            _source: str | None,
        ) -> int:
            return sqlite3.SQLITE_DENY if denied(action, arg1) else sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        return connection

    monkeypatch.setattr(db_writer_module.sqlite3, "connect", faulting_connect)

    with pytest.raises(sqlite3.DatabaseError):
        DbWriter(
            db_path=str(path),
            busy_timeout_ms=BUSY_TIMEOUT_MS,
            journal_mode=JOURNAL_MODE,
            schema_sql=load_schema_sql(SchemaState.CURRENT_V1),
        )

    check = real_connect(path)
    try:
        assert _application_objects(check) == []
        assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        check.close()


@pytest.mark.unit
def test_existing_current_v1_allows_omitted_schema_sql(tmp_path: Path) -> None:
    path = tmp_path / "current.db"
    connection = _open(path)
    connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
    connection.close()

    writer = DbWriter(
        db_path=str(path),
        busy_timeout_ms=BUSY_TIMEOUT_MS,
        journal_mode=JOURNAL_MODE,
        schema_sql=None,
    )
    try:
        assert classify_schema(writer.connection).state is SchemaState.CURRENT_V1
    finally:
        writer.close()


@pytest.mark.unit
def test_journal_setup_failure_closes_admitted_connection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "journal-failure.db"
    bootstrap = _open(path)
    bootstrap.executescript(load_schema_sql(SchemaState.CURRENT_V1))
    bootstrap.close()
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    class JournalFailureConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:  # pyright: ignore[reportIncompatibleMethodOverride]
            if sql.startswith("PRAGMA journal_mode="):
                raise sqlite3.OperationalError("injected journal failure")
            return super().execute(sql, parameters)  # type: ignore[arg-type]

    def failing_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        if args and args[0] == ":memory:":
            return real_connect(*args, **kwargs)
        kwargs["factory"] = JournalFailureConnection
        connection = real_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(db_writer_module.sqlite3, "connect", failing_connect)

    with pytest.raises(sqlite3.OperationalError, match="injected journal failure"):
        DbWriter(
            db_path=str(path),
            busy_timeout_ms=BUSY_TIMEOUT_MS,
            journal_mode=JOURNAL_MODE,
            schema_sql=None,
        )

    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_lifespan_requires_configured_schema_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    schema_path = tmp_path / "missing-schema.sql"
    lock_path = tmp_path / "maintenance.lock"
    settings = SimpleNamespace(
        recovery=SimpleNamespace(maintenance_lock_path=lock_path),
        logging=SimpleNamespace(level="INFO", directory=str(tmp_path / "logs")),
        service=SimpleNamespace(name="db-gateway", version="test"),
        server=SimpleNamespace(port=18007),
        database=SimpleNamespace(
            path=str(tmp_path / "economy.db"),
            schema_path=str(schema_path),
            busy_timeout_ms=BUSY_TIMEOUT_MS,
            journal_mode=JOURNAL_MODE,
        ),
    )
    monkeypatch.setattr(lifespan_module, "get_settings", lambda: settings)
    monkeypatch.setattr(lifespan_module, "setup_logging", lambda *_args: None)

    with pytest.raises(FileNotFoundError, match=r"missing-schema\.sql"):
        async with lifespan_module.lifespan(SimpleNamespace()):  # type: ignore[arg-type]
            pytest.fail("lifespan unexpectedly started")


@pytest.mark.unit
@pytest.mark.parametrize(
    "metadata_sql",
    [
        "UPDATE schema_metadata SET singleton = 2",
        "UPDATE schema_metadata SET version = x'31'",
        "UPDATE schema_metadata SET migrated_from_version = 0",
        "INSERT INTO schema_metadata VALUES (2, 1, NULL, NULL, NULL)",
    ],
    ids=["singleton", "version-type", "incomplete-provenance", "extra-row"],
)
def test_current_schema_requires_exact_metadata_row(tmp_path: Path, metadata_sql: str) -> None:
    connection = _open(tmp_path / "metadata-content.db")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(metadata_sql)
        connection.execute("PRAGMA ignore_check_constraints=OFF")
        assert classify_schema(connection).state is not SchemaState.CURRENT_V1
    finally:
        connection.close()


@pytest.mark.unit
def test_current_v1_requires_canonical_column_order(tmp_path: Path) -> None:
    baseline = load_schema_sql(SchemaState.CURRENT_V1)
    original = (
        '    agent_id       TEXT PRIMARY KEY,            -- "a-<uuid4>"\n'
        "    name           TEXT NOT NULL,\n"
        '    public_key     TEXT NOT NULL UNIQUE,        -- "ed25519:<base64>"\n'
    )
    reordered = (
        '    agent_id       TEXT PRIMARY KEY,            -- "a-<uuid4>"\n'
        '    public_key     TEXT NOT NULL UNIQUE,        -- "ed25519:<base64>"\n'
        "    name           TEXT NOT NULL,\n"
    )
    assert original in baseline
    connection = _open(tmp_path / "reordered-current.db")
    try:
        connection.executescript(baseline.replace(original, reordered, 1))
        assert classify_schema(connection).state is not SchemaState.CURRENT_V1
    finally:
        connection.close()


@pytest.mark.unit
def test_changed_column_collation_is_not_current(tmp_path: Path) -> None:
    baseline = load_schema_sql(SchemaState.CURRENT_V1)
    original = "name           TEXT NOT NULL,"
    assert original in baseline
    connection = _open(tmp_path / "collation.db")
    try:
        connection.executescript(
            baseline.replace(original, "name           TEXT COLLATE NOCASE NOT NULL,", 1)
        )
        assert classify_schema(connection).state is not SchemaState.CURRENT_V1
    finally:
        connection.close()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("busy_timeout_ms", "journal_mode"),
    [
        (True, "wal"),
        (-1, "wal"),
        (3_000_000_000, "wal"),
        (5000, "bogus"),
        (5000, "WAL"),
    ],
)
def test_constructor_rejects_invalid_pragma_configuration(
    tmp_path: Path, busy_timeout_ms: int, journal_mode: str
) -> None:
    path = tmp_path / "invalid-pragma.db"
    connection = _open(path)
    connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
    connection.close()

    with pytest.raises(ServiceError, match="database configuration"):
        DbWriter(
            str(path),
            busy_timeout_ms=busy_timeout_ms,
            journal_mode=journal_mode,
            schema_sql=None,
        )


@pytest.mark.unit
@pytest.mark.parametrize("ignored_pragma", ["journal_mode", "foreign_keys"])
def test_constructor_verifies_effective_pragma_postconditions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ignored_pragma: str
) -> None:
    path = tmp_path / f"ignored-{ignored_pragma}.db"
    connection = _open(path)
    connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
    connection.close()
    original_connect = sqlite3.connect

    class IgnoringPragmaConnection(sqlite3.Connection):
        def execute(self, sql: str, parameters: object = (), /) -> sqlite3.Cursor:  # pyright: ignore[reportIncompatibleMethodOverride]
            if sql in {
                f"PRAGMA {ignored_pragma}=wal",
                f"PRAGMA {ignored_pragma}=ON",
            }:
                return super().execute("SELECT 1")
            return super().execute(sql, parameters)  # type: ignore[arg-type]

    def connect(database: str, **kwargs: object) -> sqlite3.Connection:
        if str(database) == str(path):
            return original_connect(database, factory=IgnoringPragmaConnection, **kwargs)
        return original_connect(database, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(ServiceError, match="database configuration"):
        DbWriter(str(path), busy_timeout_ms=5000, journal_mode="wal", schema_sql=None)


@pytest.mark.unit
def test_bootstrap_preserves_primary_error_when_rollback_and_close_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cleanup-failures.db"
    original_connect = sqlite3.connect

    class CleanupFailureConnection(sqlite3.Connection):
        def rollback(self) -> None:
            super().rollback()
            raise RuntimeError("secondary rollback failure")

        def close(self) -> None:
            super().close()
            raise RuntimeError("secondary close failure")

    def connect(database: str, **kwargs: object) -> sqlite3.Connection:
        if str(database) == str(path):
            return original_connect(database, factory=CleanupFailureConnection, **kwargs)
        return original_connect(database, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    candidate = (
        load_schema_sql(SchemaState.CURRENT_V1)
        + "\nCREATE TABLE broken (duplicate TEXT, duplicate INTEGER);\n"
    )
    with pytest.raises(sqlite3.OperationalError, match="duplicate column") as raised:
        DbWriter(str(path), busy_timeout_ms=5000, journal_mode="wal", schema_sql=candidate)
    assert "secondary rollback failure" in "\n".join(raised.value.__notes__)
    assert "secondary close failure" in "\n".join(raised.value.__notes__)

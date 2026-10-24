"""Acceptance prewrite diagnostics must retain their original error boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from tests.fakes.mp09_acceptance_binding_fixture import (
    claim_acceptance,
    full_snapshot,
    prepare_native_acceptance,
)
from tests.fakes.mp09_receipt_adoption_fixture import (
    BOARD_KID,
    DEADLINE_ACTOR_ID,
    bank_snapshot,
)

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

pytestmark = pytest.mark.unit

_DIAGNOSTIC = "diagnostic native contract read failure"
_WRITE_MARKERS = (
    "UPDATE board_tasks SET status='accepted'",
    "UPDATE board_task_contracts SET lifecycle_version=",
    "UPDATE board_task_artifact_contracts SET accepted_binding_kind=",
    "INSERT INTO events",
    "INSERT INTO board_task_operations",
)


class _PrewriteDiagnosticCursor:
    def __init__(
        self,
        cursor: sqlite3.Cursor,
        writes_attempted: list[str],
    ) -> None:
        self._cursor = cursor
        self._writes_attempted = writes_attempted

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] | dict[str, object] = (),
    ) -> _PrewriteDiagnosticCursor:
        normalized = " ".join(sql.split())
        if any(normalized.startswith(marker) for marker in _WRITE_MARKERS):
            self._writes_attempted.append(normalized)
        if (
            normalized.startswith(
                "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
            )
            and "FROM board_task_artifact_contracts WHERE task_id=?" in normalized
        ):
            raise RuntimeError(_DIAGNOSTIC)
        self._cursor.execute(sql, parameters)
        return self

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _PrewriteDiagnosticConnection:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self.writes_attempted: list[str] = []

    def cursor(self) -> _PrewriteDiagnosticCursor:
        return _PrewriteDiagnosticCursor(
            self._connection.cursor(),
            self.writes_attempted,
        )

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def test_prewrite_native_integrity_runtime_error_is_not_relabelled_as_persistence(
    tmp_path: Path,
) -> None:
    fixture = prepare_native_acceptance(tmp_path / "prewrite-error-scope.sqlite3")
    try:
        connection = _PrewriteDiagnosticConnection(fixture.connection)
        writer = TaskOperationWriter(
            cast("sqlite3.Connection", connection),
            clock_us=fixture.clock,
            deadline_actor_id=DEADLINE_ACTOR_ID,
            platform_public_key="ed25519:test-platform-authority",
            current_task_board_caller_kid=BOARD_KID,
        )
        before = full_snapshot(fixture.connection)
        bank_before = bank_snapshot(fixture.connection)
        observed: Exception | None = None
        try:
            claim_acceptance(fixture, writer=writer)
        except Exception as exc:
            observed = exc
        assert bank_snapshot(fixture.connection) == bank_before
        assert full_snapshot(fixture.connection) == before
        assert connection.writes_attempted == []
        assert type(observed) is RuntimeError
        assert str(observed) == _DIAGNOSTIC
    finally:
        fixture.close()

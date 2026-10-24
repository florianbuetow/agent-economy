"""Bounded write and cursor fault adapters for MP-09 acceptance review tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from db_gateway_service.services.task_operation_writer import TaskOperationWriter
from tests.fakes.mp09_receipt_adoption_fixture import BOARD_KID, DEADLINE_ACTOR_ID

if TYPE_CHECKING:
    import sqlite3

    from tests.fakes.mp09_acceptance_binding_fixture import AcceptanceBindingFixture

type AcceptanceRuntimeTarget = Literal[
    "task_update",
    "timing_update",
    "artifact_update",
    "event_insert",
    "operation_insert",
]

_WRITE_MARKERS: dict[AcceptanceRuntimeTarget, tuple[str, str]] = {
    "task_update": ("UPDATE board_tasks", "status='accepted'"),
    "timing_update": ("UPDATE board_task_contracts", "accepted_reward"),
    "artifact_update": ("UPDATE board_task_artifact_contracts", "accepted_binding_kind"),
    "event_insert": ("INSERT INTO events", "VALUES"),
    "operation_insert": ("INSERT INTO board_task_operations", "VALUES"),
}


class _RuntimeCursor:
    def __init__(
        self,
        cursor: sqlite3.Cursor,
        *,
        target: AcceptanceRuntimeTarget,
    ) -> None:
        self._cursor = cursor
        self._target = target
        self._fired = False

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] | dict[str, object] = (),
    ) -> _RuntimeCursor:
        normalized = " ".join(sql.split())
        prefix, marker = _WRITE_MARKERS[self._target]
        if not self._fired and normalized.startswith(prefix) and marker in normalized:
            self._fired = True
            raise RuntimeError(f"injected {self._target} runtime failure")
        self._cursor.execute(sql, parameters)
        return self

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _LastrowidCursor:
    def __init__(
        self,
        cursor: sqlite3.Cursor,
        *,
        value: object,
        stringify_actual: bool,
    ) -> None:
        self._cursor = cursor
        self._value = value
        self._stringify_actual = stringify_actual
        self._selected = False

    def execute(
        self,
        sql: str,
        parameters: tuple[object, ...] | dict[str, object] = (),
    ) -> _LastrowidCursor:
        normalized = " ".join(sql.split())
        self._cursor.execute(sql, parameters)
        self._selected = normalized.startswith("INSERT INTO events") and "VALUES" in normalized
        return self

    @property
    def lastrowid(self) -> object:
        if not self._selected:
            return self._cursor.lastrowid
        if self._stringify_actual:
            return str(self._cursor.lastrowid)
        return self._value

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _ReviewConnection:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        runtime_target: AcceptanceRuntimeTarget | None,
        lastrowid: object,
        override_lastrowid: bool,
        stringify_actual_lastrowid: bool,
    ) -> None:
        self._connection = connection
        self._runtime_target = runtime_target
        self._lastrowid = lastrowid
        self._override_lastrowid = override_lastrowid
        self._stringify_actual_lastrowid = stringify_actual_lastrowid

    def cursor(self) -> _RuntimeCursor | _LastrowidCursor | sqlite3.Cursor:
        cursor = self._connection.cursor()
        if self._runtime_target is not None:
            return _RuntimeCursor(cursor, target=self._runtime_target)
        if self._override_lastrowid:
            return _LastrowidCursor(
                cursor,
                value=self._lastrowid,
                stringify_actual=self._stringify_actual_lastrowid,
            )
        return cursor

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _writer(
    fixture: AcceptanceBindingFixture,
    connection: _ReviewConnection,
) -> TaskOperationWriter:
    return TaskOperationWriter(
        cast("sqlite3.Connection", connection),
        clock_us=fixture.clock,
        deadline_actor_id=DEADLINE_ACTOR_ID,
        platform_public_key="ed25519:test-platform-authority",
        current_task_board_caller_kid=BOARD_KID,
    )


def runtime_failure_writer(
    fixture: AcceptanceBindingFixture,
    *,
    target: AcceptanceRuntimeTarget,
) -> TaskOperationWriter:
    """Inject one RuntimeError at the exact selected acceptance SQL write."""
    connection = _ReviewConnection(
        fixture.connection,
        runtime_target=target,
        lastrowid=None,
        override_lastrowid=False,
        stringify_actual_lastrowid=False,
    )
    return _writer(fixture, connection)


def event_identity_writer(
    fixture: AcceptanceBindingFixture,
    *,
    lastrowid: object,
) -> TaskOperationWriter:
    """Execute the real event insert but report one controlled cursor identity."""
    connection = _ReviewConnection(
        fixture.connection,
        runtime_target=None,
        lastrowid=lastrowid,
        override_lastrowid=True,
        stringify_actual_lastrowid=False,
    )
    return _writer(fixture, connection)


def numeric_string_event_identity_writer(
    fixture: AcceptanceBindingFixture,
) -> TaskOperationWriter:
    """Execute the real event insert but expose its positive identity as text."""
    connection = _ReviewConnection(
        fixture.connection,
        runtime_target=None,
        lastrowid=None,
        override_lastrowid=True,
        stringify_actual_lastrowid=True,
    )
    return _writer(fixture, connection)


__all__ = [
    "AcceptanceRuntimeTarget",
    "event_identity_writer",
    "numeric_string_event_identity_writer",
    "runtime_failure_writer",
]

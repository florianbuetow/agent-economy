"""Tests for FeedbackStore robustness and domain exception handling.

Covers fixes for:
- DuplicateFeedbackError domain exception (replaces leaked sqlite3.IntegrityError)
- ROLLBACK safety (original exception preserved when ROLLBACK fails)
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pytest

from reputation_service.services.feedback_store import DuplicateFeedbackError, FeedbackStore

if TYPE_CHECKING:
    from pathlib import Path


def _make_store(tmp_path: Path) -> FeedbackStore:
    """Create a FeedbackStore with a temp database."""
    return FeedbackStore(db_path=str(tmp_path / "test.db"))


def _insert_once(store: FeedbackStore) -> None:
    """Insert a single feedback record."""
    store.insert_feedback(
        task_id="t-1",
        from_agent_id="a-alice",
        to_agent_id="a-bob",
        category="delivery_quality",
        rating="satisfied",
        comment=None,
    )


class _FailingRollbackConn:
    """Wrapper around sqlite3.Connection that makes ROLLBACK fail."""

    def __init__(self, real_conn: sqlite3.Connection) -> None:
        self._real = real_conn

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        if sql == "ROLLBACK":
            raise sqlite3.OperationalError("disk I/O error during rollback")
        return self._real.execute(sql, parameters)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


class _FailingInsertConn:
    """Wrapper that raises OperationalError on the first INSERT, and makes ROLLBACK fail."""

    def __init__(self, real_conn: sqlite3.Connection) -> None:
        self._real = real_conn
        self._insert_count = 0

    def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
        if sql == "ROLLBACK":
            raise sqlite3.OperationalError("disk I/O error during rollback")
        if sql.strip().startswith("INSERT"):
            self._insert_count += 1
            if self._insert_count == 1:
                # Let the INSERT execute, then raise to simulate disk-full after write
                self._real.execute(sql, parameters)
                raise sqlite3.OperationalError("disk full")
        return self._real.execute(sql, parameters)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@pytest.mark.unit
class TestDuplicateFeedbackDomainException:
    """FeedbackStore raises DuplicateFeedbackError, not sqlite3.IntegrityError."""

    def test_duplicate_raises_domain_exception(self, tmp_path: Path) -> None:
        """Duplicate insert raises DuplicateFeedbackError."""
        store = _make_store(tmp_path)
        _insert_once(store)
        with pytest.raises(DuplicateFeedbackError):
            _insert_once(store)

    def test_domain_exception_is_not_sqlite_error(self, tmp_path: Path) -> None:
        """DuplicateFeedbackError is not a subclass of sqlite3.Error."""
        store = _make_store(tmp_path)
        _insert_once(store)
        with pytest.raises(DuplicateFeedbackError) as exc_info:
            _insert_once(store)
        assert not isinstance(exc_info.value, sqlite3.Error)


@pytest.mark.unit
class TestRollbackSafety:
    """Original exceptions preserved even if ROLLBACK fails."""

    def test_duplicate_error_preserved_when_rollback_fails(self, tmp_path: Path) -> None:
        """DuplicateFeedbackError raised even if ROLLBACK throws."""
        store = _make_store(tmp_path)
        _insert_once(store)

        store._db = _FailingRollbackConn(store._db)  # type: ignore[assignment]

        with pytest.raises(DuplicateFeedbackError):
            _insert_once(store)

    def test_generic_error_preserved_when_rollback_fails(self, tmp_path: Path) -> None:
        """Non-integrity errors preserved even if ROLLBACK throws."""
        store = _make_store(tmp_path)

        store._db = _FailingInsertConn(store._db)  # type: ignore[assignment]

        with pytest.raises(sqlite3.OperationalError, match="disk full"):
            _insert_once(store)

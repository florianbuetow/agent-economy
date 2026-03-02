"""Cross-service integration test fixtures."""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


SCHEMA_PATH = Path(__file__).parent.parent.parent / "docs" / "specifications" / "schema.sql"


@pytest.fixture
def schema_sql() -> str:
    """Load the shared economy.db schema."""
    return SCHEMA_PATH.read_text()


@pytest.fixture
def tmp_db_path() -> Iterator[str]:
    """Create a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as file_handle:
        path = file_handle.name
    yield path
    with contextlib.suppress(OSError):
        Path(path).unlink()


@pytest.fixture
def initialized_db(tmp_db_path: str, schema_sql: str) -> str:
    """Create a temporary database initialized with schema.sql."""
    conn = sqlite3.connect(tmp_db_path)
    conn.executescript(schema_sql)
    conn.close()
    return tmp_db_path


def read_db(db_path: str, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Read rows from SQLite as dicts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def read_one(db_path: str, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Read a single SQLite row as dict."""
    rows = read_db(db_path, query, params)
    return rows[0] if rows else None


def count_rows(db_path: str, table: str) -> int:
    """Count rows in a table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
    count = int(cursor.fetchone()[0])
    conn.close()
    return count


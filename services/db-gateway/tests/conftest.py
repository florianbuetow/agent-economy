"""Root conftest — shared fixtures for all test types."""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def schema_sql() -> str:
    """Load the shared economy.db schema."""
    base = Path(__file__).parent.parent.parent.parent
    schema_path = base / "docs" / "specifications" / "schema.sql"
    return schema_path.read_text()


@pytest.fixture
def tmp_db_path() -> Iterator[str]:
    """Create a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    with contextlib.suppress(OSError):
        Path(path).unlink()


@pytest.fixture
def initialized_db(tmp_db_path: str, schema_sql: str) -> str:
    """Create a temporary database with the schema initialized."""
    conn = sqlite3.connect(tmp_db_path)
    conn.executescript(schema_sql)
    conn.close()
    return tmp_db_path


def make_event(
    source: str = "identity",
    event_type: str = "agent.registered",
    task_id: str | None = None,
    agent_id: str | None = None,
    summary: str = "Test event",
    payload: str = "{}",
) -> dict[str, Any]:
    """Helper to construct a valid event dict."""
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": "2026-02-28T10:00:00Z",
        "task_id": task_id,
        "agent_id": agent_id,
        "summary": summary,
        "payload": payload,
    }

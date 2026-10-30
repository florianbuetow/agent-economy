"""Root conftest — shared fixtures for all test types."""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from db_gateway_service.schema import SchemaState, load_schema_sql

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def schema_sql() -> str:
    """Load the exact composed V5 schema admitted by the current runtime."""
    return load_schema_sql(SchemaState.CURRENT_V6)


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

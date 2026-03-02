"""DB helper fixtures for E2E tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .db_helpers import get_writable_db_connection

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def e2e_writable_db(e2e_db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Provide a writable DB connection for test mutations."""
    conn = get_writable_db_connection(str(e2e_db_path))
    yield conn
    conn.close()

"""Extended E2E fixtures — richer seed data for comprehensive browser tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="session", autouse=True)
def e2e_extended_seed(e2e_db_path: Path) -> None:
    """Extend the base seed data with additional agents, tasks, and events."""
    from .seed_db import extend_seed_data  # noqa: PLC0415

    conn = sqlite3.connect(str(e2e_db_path))
    try:
        extend_seed_data(conn)
    finally:
        conn.close()

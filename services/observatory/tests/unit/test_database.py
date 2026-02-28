"""Tests for database connection and query helpers."""

import aiosqlite
import pytest

from observatory_service.services.database import execute_query, execute_query_one


@pytest.mark.unit
async def test_execute_query_returns_rows(tmp_path):
    """execute_query returns list of Row objects."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE t (id INTEGER, name TEXT)")
        await db.execute("INSERT INTO t VALUES (1, 'alice')")
        await db.commit()
        rows = await execute_query(db, "SELECT * FROM t")
    assert len(rows) == 1
    assert rows[0]["name"] == "alice"


@pytest.mark.unit
async def test_execute_query_one_returns_single_row(tmp_path):
    """execute_query_one returns a single Row or None."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE t (id INTEGER)")
        await db.execute("INSERT INTO t VALUES (1)")
        await db.commit()
        row = await execute_query_one(db, "SELECT * FROM t WHERE id = 1")
    assert row is not None
    assert row["id"] == 1


@pytest.mark.unit
async def test_execute_query_one_returns_none_when_missing(tmp_path):
    """execute_query_one returns None when no row matches."""
    db_path = tmp_path / "test.db"
    async with aiosqlite.connect(str(db_path)) as db:
        await db.execute("CREATE TABLE t (id INTEGER)")
        await db.commit()
        row = await execute_query_one(db, "SELECT * FROM t WHERE id = 999")
    assert row is None

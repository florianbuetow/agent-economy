"""DbReader service-layer tests — direct method calls without HTTP."""

from __future__ import annotations

import sqlite3

import pytest

from db_gateway_service.services.db_reader import DbReader


def _insert_agent(
    conn: sqlite3.Connection,
    agent_id: str,
    name: str,
    public_key: str,
    registered_at: str,
) -> None:
    """Insert an identity agent row directly into SQLite."""
    conn.execute(
        "INSERT INTO identity_agents (agent_id, name, public_key, registered_at) "
        "VALUES (?, ?, ?, ?)",
        (agent_id, name, public_key, registered_at),
    )
    conn.commit()


@pytest.mark.unit
class TestDbReaderIdentity:
    """Direct DbReader tests for identity read operations."""

    def test_get_agent_exists(self, initialized_db: str) -> None:
        """get_agent returns a full record when the agent exists."""
        conn = sqlite3.connect(initialized_db)
        _insert_agent(
            conn=conn,
            agent_id="a-1",
            name="Alice",
            public_key="ed25519:key-1",
            registered_at="2026-03-01T10:00:00Z",
        )
        reader = DbReader(db=conn)

        result = reader.get_agent("a-1")

        assert result is not None
        assert result["agent_id"] == "a-1"
        assert result["name"] == "Alice"
        assert result["public_key"] == "ed25519:key-1"
        assert result["registered_at"] == "2026-03-01T10:00:00Z"
        conn.close()

    def test_get_agent_missing(self, initialized_db: str) -> None:
        """get_agent returns None for a missing agent_id."""
        conn = sqlite3.connect(initialized_db)
        reader = DbReader(db=conn)

        result = reader.get_agent("a-missing")

        assert result is None
        conn.close()

    def test_list_agents_empty(self, initialized_db: str) -> None:
        """list_agents returns empty list when no rows exist."""
        conn = sqlite3.connect(initialized_db)
        reader = DbReader(db=conn)

        result = reader.list_agents()

        assert result == []
        conn.close()

    def test_list_agents_all(self, initialized_db: str) -> None:
        """list_agents returns all rows sorted by registered_at."""
        conn = sqlite3.connect(initialized_db)
        _insert_agent(
            conn=conn,
            agent_id="a-2",
            name="Bob",
            public_key="ed25519:key-2",
            registered_at="2026-03-01T11:00:00Z",
        )
        _insert_agent(
            conn=conn,
            agent_id="a-1",
            name="Alice",
            public_key="ed25519:key-1",
            registered_at="2026-03-01T10:00:00Z",
        )
        _insert_agent(
            conn=conn,
            agent_id="a-3",
            name="Charlie",
            public_key="ed25519:key-3",
            registered_at="2026-03-01T12:00:00Z",
        )
        reader = DbReader(db=conn)

        result = reader.list_agents()

        assert len(result) == 3
        assert [agent["agent_id"] for agent in result] == ["a-1", "a-2", "a-3"]
        conn.close()

    def test_list_agents_filter_public_key(self, initialized_db: str) -> None:
        """list_agents(public_key=...) returns only the matching agent."""
        conn = sqlite3.connect(initialized_db)
        _insert_agent(
            conn=conn,
            agent_id="a-1",
            name="Alice",
            public_key="ed25519:key-1",
            registered_at="2026-03-01T10:00:00Z",
        )
        _insert_agent(
            conn=conn,
            agent_id="a-2",
            name="Bob",
            public_key="ed25519:key-2",
            registered_at="2026-03-01T11:00:00Z",
        )
        reader = DbReader(db=conn)

        result = reader.list_agents(public_key="ed25519:key-2")

        assert len(result) == 1
        assert result[0]["agent_id"] == "a-2"
        assert result[0]["public_key"] == "ed25519:key-2"
        conn.close()

    def test_count_agents(self, initialized_db: str) -> None:
        """count_agents returns the number of registered agents."""
        conn = sqlite3.connect(initialized_db)
        _insert_agent(
            conn=conn,
            agent_id="a-1",
            name="Alice",
            public_key="ed25519:key-1",
            registered_at="2026-03-01T10:00:00Z",
        )
        _insert_agent(
            conn=conn,
            agent_id="a-2",
            name="Bob",
            public_key="ed25519:key-2",
            registered_at="2026-03-01T11:00:00Z",
        )
        reader = DbReader(db=conn)

        result = reader.count_agents()

        assert result == 2
        conn.close()

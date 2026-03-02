"""Identity read endpoint tests."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from db_gateway_service.core.state import get_app_state
from db_gateway_service.services.db_reader import DbReader
from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _wire_db_reader() -> None:
    """Attach a DbReader to the test app state using the existing writer connection."""
    state = get_app_state()
    assert state.db_writer is not None
    state.db_reader = DbReader(db=state.db_writer._db)


def _register_agent(
    client: TestClient,
    agent_id: str,
    name: str,
    public_key: str,
    registered_at: str,
) -> None:
    """Register an agent via POST /identity/agents."""
    response = client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": name,
            "public_key": public_key,
            "registered_at": registered_at,
            "event": make_event(),
        },
    )
    assert response.status_code == 201


@pytest.mark.unit
class TestIdentityReads:
    """Tests for identity read endpoints."""

    def test_get_agent_by_id(self, app_with_writer: TestClient) -> None:
        """GET /identity/agents/{agent_id} returns the full record."""
        agent_id = f"a-{uuid4()}"
        public_key = f"ed25519:{uuid4()}"
        _register_agent(
            client=app_with_writer,
            agent_id=agent_id,
            name="Alice",
            public_key=public_key,
            registered_at="2026-03-01T10:00:00Z",
        )
        _wire_db_reader()

        response = app_with_writer.get(f"/identity/agents/{agent_id}")

        assert response.status_code == 200
        data = response.json()
        assert data["agent_id"] == agent_id
        assert data["name"] == "Alice"
        assert data["public_key"] == public_key
        assert data["registered_at"] == "2026-03-01T10:00:00Z"

    def test_get_agent_not_found(self, app_with_writer: TestClient) -> None:
        """GET /identity/agents/{agent_id} returns 404 when missing."""
        _wire_db_reader()

        response = app_with_writer.get("/identity/agents/a-nonexistent")

        assert response.status_code == 404
        assert response.json()["error"] == "agent_not_found"

    def test_list_agents_empty(self, app_with_writer: TestClient) -> None:
        """GET /identity/agents returns an empty list when no agents exist."""
        _wire_db_reader()

        response = app_with_writer.get("/identity/agents")

        assert response.status_code == 200
        assert response.json() == {"agents": []}

    def test_list_agents_returns_all(self, app_with_writer: TestClient) -> None:
        """GET /identity/agents returns all agents."""
        agent_id_1 = f"a-{uuid4()}"
        agent_id_2 = f"a-{uuid4()}"
        public_key_1 = f"ed25519:{uuid4()}"
        public_key_2 = f"ed25519:{uuid4()}"
        _register_agent(
            client=app_with_writer,
            agent_id=agent_id_1,
            name="Alice",
            public_key=public_key_1,
            registered_at="2026-03-01T10:00:00Z",
        )
        _register_agent(
            client=app_with_writer,
            agent_id=agent_id_2,
            name="Bob",
            public_key=public_key_2,
            registered_at="2026-03-01T11:00:00Z",
        )
        _wire_db_reader()

        response = app_with_writer.get("/identity/agents")

        assert response.status_code == 200
        agents = response.json()["agents"]
        assert len(agents) == 2
        assert agents[0]["agent_id"] == agent_id_1
        assert agents[1]["agent_id"] == agent_id_2

    def test_list_agents_filter_by_public_key(self, app_with_writer: TestClient) -> None:
        """GET /identity/agents filtered by public_key returns matching record."""
        matching_key = f"ed25519:{uuid4()}"
        _register_agent(
            client=app_with_writer,
            agent_id=f"a-{uuid4()}",
            name="Alice",
            public_key=matching_key,
            registered_at="2026-03-01T10:00:00Z",
        )
        _register_agent(
            client=app_with_writer,
            agent_id=f"a-{uuid4()}",
            name="Bob",
            public_key=f"ed25519:{uuid4()}",
            registered_at="2026-03-01T11:00:00Z",
        )
        _wire_db_reader()

        response = app_with_writer.get(f"/identity/agents?public_key={matching_key}")

        assert response.status_code == 200
        agents = response.json()["agents"]
        assert len(agents) == 1
        assert agents[0]["public_key"] == matching_key

    def test_list_agents_filter_no_match(self, app_with_writer: TestClient) -> None:
        """GET /identity/agents filtered by unknown key returns empty list."""
        _register_agent(
            client=app_with_writer,
            agent_id=f"a-{uuid4()}",
            name="Alice",
            public_key=f"ed25519:{uuid4()}",
            registered_at="2026-03-01T10:00:00Z",
        )
        _register_agent(
            client=app_with_writer,
            agent_id=f"a-{uuid4()}",
            name="Bob",
            public_key=f"ed25519:{uuid4()}",
            registered_at="2026-03-01T11:00:00Z",
        )
        _wire_db_reader()

        response = app_with_writer.get("/identity/agents?public_key=nonexistent")

        assert response.status_code == 200
        assert response.json() == {"agents": []}

    def test_count_agents_zero(self, app_with_writer: TestClient) -> None:
        """GET /identity/agents/count returns zero when empty."""
        _wire_db_reader()

        response = app_with_writer.get("/identity/agents/count")

        assert response.status_code == 200
        assert response.json() == {"count": 0}

    def test_count_agents_after_registrations(self, app_with_writer: TestClient) -> None:
        """GET /identity/agents/count reflects inserted agents."""
        _register_agent(
            client=app_with_writer,
            agent_id=f"a-{uuid4()}",
            name="Alice",
            public_key=f"ed25519:{uuid4()}",
            registered_at="2026-03-01T10:00:00Z",
        )
        _register_agent(
            client=app_with_writer,
            agent_id=f"a-{uuid4()}",
            name="Bob",
            public_key=f"ed25519:{uuid4()}",
            registered_at="2026-03-01T11:00:00Z",
        )
        _register_agent(
            client=app_with_writer,
            agent_id=f"a-{uuid4()}",
            name="Charlie",
            public_key=f"ed25519:{uuid4()}",
            registered_at="2026-03-01T12:00:00Z",
        )
        _wire_db_reader()

        response = app_with_writer.get("/identity/agents/count")

        assert response.status_code == 200
        assert response.json() == {"count": 3}

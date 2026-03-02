"""Agent registration tests — AGT-01 through AGT-13."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from tests.conftest import make_event

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from db_gateway_service.services.db_writer import DbWriter


@pytest.mark.unit
class TestAgentRegistration:
    """Tests for POST /identity/agents."""

    def test_register_valid_agent(self, app_with_writer: TestClient) -> None:
        """AGT-01: Register a valid agent."""
        aid = f"a-{uuid4()}"
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["agent_id"] == aid
        assert "event_id" in data
        assert isinstance(data["event_id"], int)
        assert data["event_id"] > 0

    def test_register_two_agents_different_keys(self, app_with_writer: TestClient) -> None:
        """AGT-02: Register two agents with different keys."""
        resp1 = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        resp2 = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "name": "Bob",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert resp1.status_code == 201
        assert resp2.status_code == 201
        eid1 = resp1.json()["event_id"]
        eid2 = resp2.json()["event_id"]
        assert eid1 != eid2
        assert eid2 > eid1

    def test_duplicate_public_key_rejected(self, app_with_writer: TestClient) -> None:
        """AGT-03: Duplicate public key is rejected."""
        shared_key = f"ed25519:{uuid4()}"
        app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "name": "Alice",
                "public_key": shared_key,
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "name": "Bob",
                "public_key": shared_key,
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert resp.status_code == 409
        assert resp.json()["error"] == "public_key_exists"

    def test_idempotent_replay_returns_existing(self, app_with_writer: TestClient) -> None:
        """AGT-04: Idempotent replay returns existing agent."""
        aid = f"a-{uuid4()}"
        pk = f"ed25519:{uuid4()}"
        body = {
            "agent_id": aid,
            "name": "Alice",
            "public_key": pk,
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        }
        resp1 = app_with_writer.post("/identity/agents", json=body)
        assert resp1.status_code == 201

        resp2 = app_with_writer.post("/identity/agents", json=body)
        assert resp2.status_code in (200, 201)
        assert resp2.json()["agent_id"] == aid

    def test_missing_name(self, app_with_writer: TestClient) -> None:
        """AGT-05: Missing name returns 400 missing_field."""
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_public_key(self, app_with_writer: TestClient) -> None:
        """AGT-06: Missing public_key returns 400 missing_field."""
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "name": "Alice",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_agent_id(self, app_with_writer: TestClient) -> None:
        """AGT-07: Missing agent_id returns 400 missing_field."""
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_registered_at(self, app_with_writer: TestClient) -> None:
        """AGT-08: Missing registered_at returns 400 missing_field."""
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "event": make_event(),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_missing_event(self, app_with_writer: TestClient) -> None:
        """AGT-09: Missing event object returns 400 missing_field."""
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_event_missing_required_fields(self, app_with_writer: TestClient) -> None:
        """AGT-10: Event with missing required fields returns 400 missing_field."""
        incomplete_event = {
            "event_type": "agent.registered",
            "timestamp": "2026-02-28T10:00:00Z",
            "summary": "Test",
            "payload": "{}",
            # event_source is omitted
        }
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": f"a-{uuid4()}",
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": incomplete_event,
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_null_required_fields(self, app_with_writer: TestClient) -> None:
        """AGT-11: Null required fields return 400 missing_field."""
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": None,
                "name": None,
                "public_key": None,
                "registered_at": None,
                "event": make_event(),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "missing_field"

    def test_event_written_atomically(
        self, app_with_writer: TestClient, db_writer: DbWriter
    ) -> None:
        """AGT-12: Event is written atomically with the agent."""
        aid = f"a-{uuid4()}"
        resp = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": aid,
                "name": "Alice",
                "public_key": f"ed25519:{uuid4()}",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert resp.status_code == 201
        event_id = resp.json()["event_id"]

        # Query events table directly
        cursor = db_writer._db.execute(
            "SELECT event_id, event_source, event_type FROM events WHERE event_id = ?",
            (event_id,),
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == event_id
        assert row[1] == "identity"
        assert row[2] == "agent.registered"

    def test_malformed_json_body(self, app_with_writer: TestClient) -> None:
        """AGT-13: Malformed JSON body returns 400 invalid_json."""
        resp = app_with_writer.post(
            "/identity/agents",
            content=b"{invalid",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_json"

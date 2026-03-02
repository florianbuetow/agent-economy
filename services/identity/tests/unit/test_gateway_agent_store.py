"""Unit tests for GatewayAgentStore."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from identity_service.services.agent_store import DuplicateAgentError
from identity_service.services.gateway_agent_store import GatewayAgentStore


def _mock_response(status_code: int, json_data: Any = None, text: str = "") -> MagicMock:
    """Create a mock HTTP response."""
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    if json_data is not None:
        response.json.return_value = json_data
    return response


@pytest.mark.unit
class TestGatewayAgentStoreInsert:
    """Tests for GatewayAgentStore.insert."""

    @pytest.mark.asyncio
    async def test_insert_success(self) -> None:
        """Successful insert returns agent record."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(201, {"agent_id": "a-123", "event_id": 1})
        store._client.post = AsyncMock(return_value=mock_resp)

        result = await store.insert("Alice", "ed25519:abc123")
        assert result["name"] == "Alice"
        assert result["public_key"] == "ed25519:abc123"
        assert result["agent_id"].startswith("a-")
        assert "registered_at" in result

        await store.close()

    @pytest.mark.asyncio
    async def test_insert_duplicate_raises(self) -> None:
        """Duplicate public key returns 409 and raises DuplicateAgentError."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(
            409,
            {"error": "public_key_exists", "message": "Already registered"},
        )
        store._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(DuplicateAgentError):
            await store.insert("Alice", "ed25519:abc123")

        await store.close()

    @pytest.mark.asyncio
    async def test_insert_server_error_raises(self) -> None:
        """5xx from gateway raises RuntimeError."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(500, text="Internal Server Error")
        store._client.post = AsyncMock(return_value=mock_resp)

        with pytest.raises(RuntimeError, match="Gateway error"):
            await store.insert("Alice", "ed25519:abc123")

        await store.close()


@pytest.mark.unit
class TestGatewayAgentStoreGetById:
    """Tests for GatewayAgentStore.get_by_id."""

    @pytest.mark.asyncio
    async def test_get_existing_agent(self) -> None:
        """Existing agent returns full record."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        agent_data = {
            "agent_id": "a-123",
            "name": "Alice",
            "public_key": "ed25519:abc",
            "registered_at": "2026-01-01T00:00:00Z",
        }
        mock_resp = _mock_response(200, agent_data)
        store._client.get = AsyncMock(return_value=mock_resp)

        result = await store.get_by_id("a-123")
        assert result is not None
        assert result["agent_id"] == "a-123"
        assert result["name"] == "Alice"

        await store.close()

    @pytest.mark.asyncio
    async def test_get_missing_agent(self) -> None:
        """Missing agent returns None."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(404, {"error": "agent_not_found"})
        store._client.get = AsyncMock(return_value=mock_resp)

        result = await store.get_by_id("a-nonexistent")
        assert result is None

        await store.close()


@pytest.mark.unit
class TestGatewayAgentStoreListAll:
    """Tests for GatewayAgentStore.list_all."""

    @pytest.mark.asyncio
    async def test_list_empty(self) -> None:
        """Empty DB returns empty list."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(200, {"agents": []})
        store._client.get = AsyncMock(return_value=mock_resp)

        result = await store.list_all()
        assert result == []

        await store.close()

    @pytest.mark.asyncio
    async def test_list_agents_omits_public_key(self) -> None:
        """list_all omits public_key from results."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(
            200,
            {
                "agents": [
                    {
                        "agent_id": "a-1",
                        "name": "Alice",
                        "public_key": "ed25519:abc",
                        "registered_at": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )
        store._client.get = AsyncMock(return_value=mock_resp)

        result = await store.list_all()
        assert len(result) == 1
        assert "public_key" not in result[0]

        await store.close()


@pytest.mark.unit
class TestGatewayAgentStoreCount:
    """Tests for GatewayAgentStore.count."""

    @pytest.mark.asyncio
    async def test_count_zero(self) -> None:
        """Count returns 0 for empty DB."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(200, {"count": 0})
        store._client.get = AsyncMock(return_value=mock_resp)

        result = await store.count()
        assert result == 0

        await store.close()

    @pytest.mark.asyncio
    async def test_count_positive(self) -> None:
        """Count returns correct number."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(200, {"count": 5})
        store._client.get = AsyncMock(return_value=mock_resp)

        result = await store.count()
        assert result == 5

        await store.close()

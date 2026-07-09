# Identity Service Gateway Migration — Implementation Plan

## Overview

Remove the identity service's dependency on local SQLite. All reads and writes go through the DB Gateway HTTP API. This plan has 4 tiers executed in order.

## Prerequisites

Read these files FIRST before starting any tier:
1. `AGENTS.md` — project conventions
2. `docs/specifications/schema.sql` — unified schema
3. `services/db-gateway/src/db_gateway_service/services/db_writer.py` — current gateway writer
4. `services/db-gateway/src/db_gateway_service/routers/identity.py` — current identity write endpoint
5. `services/db-gateway/tests/unit/conftest.py` — test fixture patterns
6. `services/identity/src/identity_service/services/agent_store.py` — current SQLite store
7. `services/identity/src/identity_service/services/agent_registry.py` — business logic layer
8. `services/identity/src/identity_service/services/gateway_client.py` — current fire-and-forget client
9. `services/identity/src/identity_service/core/lifespan.py` — startup wiring
10. `services/identity/src/identity_service/core/state.py` — app state
11. `services/identity/src/identity_service/routers/agents.py` — HTTP endpoints

Use `uv run` for all Python execution. Never use raw `python`, `python3`, or `pip install`.
Do NOT use git. There is no git repository.
Do NOT modify existing test files in `tests/` — add new test files instead.

---

## Tier 1: Add GET Read Endpoints to DB Gateway (Identity Domain Only)

**Goal:** The DB Gateway can serve identity read queries so services don't need local SQLite.

### Step 1.1: Create `db_reader.py`

**File:** `services/db-gateway/src/db_gateway_service/services/db_reader.py` (NEW)

```python
"""Database reader — SQLite query executor for the Database Gateway."""

from __future__ import annotations

import sqlite3
from typing import Any


class DbReader:
    """
    SQLite query executor for read operations.

    Shares the same database connection as DbWriter.
    All methods are read-only SELECT queries.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self._db.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        """Get a single agent by ID. Returns None if not found."""
        cursor = self._db.execute(
            "SELECT agent_id, name, public_key, registered_at "
            "FROM identity_agents WHERE agent_id = ?",
            (agent_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {
            "agent_id": row["agent_id"],
            "name": row["name"],
            "public_key": row["public_key"],
            "registered_at": row["registered_at"],
        }

    def list_agents(self, public_key: str | None = None) -> list[dict[str, Any]]:
        """
        List all agents, optionally filtered by public_key.

        If public_key is provided, returns only the matching agent.
        Returns list of agent dicts sorted by registered_at.
        """
        if public_key is not None:
            cursor = self._db.execute(
                "SELECT agent_id, name, public_key, registered_at "
                "FROM identity_agents WHERE public_key = ? "
                "ORDER BY registered_at",
                (public_key,),
            )
        else:
            cursor = self._db.execute(
                "SELECT agent_id, name, public_key, registered_at "
                "FROM identity_agents ORDER BY registered_at"
            )
        rows = cursor.fetchall()
        return [
            {
                "agent_id": row["agent_id"],
                "name": row["name"],
                "public_key": row["public_key"],
                "registered_at": row["registered_at"],
            }
            for row in rows
        ]

    def count_agents(self) -> int:
        """Count total registered agents."""
        cursor = self._db.execute("SELECT COUNT(*) FROM identity_agents")
        row = cursor.fetchone()
        return int(row[0]) if row else 0
```

### Step 1.2: Wire DbReader into AppState

**File:** `services/db-gateway/src/db_gateway_service/core/state.py`

Add `db_reader: DbReader | None = None` field to `AppState` dataclass.
Add the TYPE_CHECKING import for `DbReader`.

### Step 1.3: Initialize DbReader in lifespan

**File:** `services/db-gateway/src/db_gateway_service/core/lifespan.py`

After creating `DbWriter`, create `DbReader` sharing the same SQLite connection:
```python
from db_gateway_service.services.db_reader import DbReader

# After state.db_writer = DbWriter(...)
state.db_reader = DbReader(db=state.db_writer._db)
```

### Step 1.4: Add GET endpoints to identity router

**File:** `services/db-gateway/src/db_gateway_service/routers/identity.py`

Add these 3 GET endpoints AFTER the existing POST endpoint:

```python
@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> JSONResponse:
    """Get a single agent by ID."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    agent = state.db_reader.get_agent(agent_id)
    if agent is None:
        raise ServiceError(
            error="agent_not_found",
            message="No agent with this agent_id",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=agent)


@router.get("/agents")
async def list_agents(request: Request) -> JSONResponse:
    """List all agents, optionally filtered by public_key query param."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    public_key = request.query_params.get("public_key")
    agents = state.db_reader.list_agents(public_key=public_key)
    return JSONResponse(status_code=200, content={"agents": agents})


@router.get("/agents/count")
async def count_agents() -> JSONResponse:
    """Count total registered agents."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    count = state.db_reader.count_agents()
    return JSONResponse(status_code=200, content={"count": count})
```

**IMPORTANT:** The GET `/agents` and GET `/agents/count` routes MUST be defined BEFORE GET `/agents/{agent_id}` to avoid FastAPI treating "count" as an agent_id. Reorder the endpoints so `/agents/count` comes first, then `/agents`, then `/agents/{agent_id}`.

### Step 1.5: Write tests for read endpoints

**File:** `services/db-gateway/tests/unit/routers/test_identity_reads.py` (NEW)

Write tests following the pattern in `test_identity.py`. Use the `app_with_writer` fixture.
For each test, first POST an agent via the write endpoint, then GET it via the read endpoint.

Tests to write:
1. `test_get_agent_by_id` — Register agent, GET /identity/agents/{id}, verify 200 with full record
2. `test_get_agent_not_found` — GET /identity/agents/a-nonexistent, verify 404 with error "agent_not_found"
3. `test_list_agents_empty` — GET /identity/agents, verify 200 with empty list
4. `test_list_agents_returns_all` — Register 2 agents, GET /identity/agents, verify both returned
5. `test_list_agents_filter_by_public_key` — Register 2 agents, GET /identity/agents?public_key=..., verify only 1 returned
6. `test_list_agents_filter_no_match` — GET /identity/agents?public_key=nonexistent, verify empty list
7. `test_count_agents_zero` — GET /identity/agents/count, verify {"count": 0}
8. `test_count_agents_after_registrations` — Register 3 agents, GET /identity/agents/count, verify {"count": 3}

All tests must be decorated with `@pytest.mark.unit`.

### Step 1.6: Write tests for DbReader

**File:** `services/db-gateway/tests/unit/test_db_reader.py` (NEW)

Test `DbReader` methods directly (not through HTTP). Use the `initialized_db` fixture from `tests/conftest.py` to create a DbReader with a real SQLite connection. Insert test data with direct SQL, then verify reads.

Tests:
1. `test_get_agent_exists` — Insert row directly, read via get_agent, verify dict
2. `test_get_agent_missing` — get_agent with nonexistent ID returns None
3. `test_list_agents_empty` — No rows, list_agents returns empty list
4. `test_list_agents_all` — Insert 3 rows, list_agents returns all 3 sorted
5. `test_list_agents_filter_public_key` — Insert 2, filter by one key, get 1
6. `test_count_agents` — Insert 2, count returns 2

All tests must be decorated with `@pytest.mark.unit`.

### Step 1.7: Verify

Run from `services/db-gateway/`:
```bash
cd services/db-gateway && uv run pytest tests/ -x -q
```

If tests pass, run full CI:
```bash
cd services/db-gateway && just ci-quiet
```

Fix any issues before proceeding to Tier 2.

---

## Tier 2: Create IdentityStorageInterface Protocol

**Goal:** Define an async Protocol for the identity service's data access layer.

### Step 2.1: Create protocol file

**File:** `services/identity/src/identity_service/services/protocol.py` (NEW)

```python
"""Storage interface protocol for the Identity service."""

from __future__ import annotations

from typing import Protocol


class IdentityStorageInterface(Protocol):
    """
    Async storage interface for agent identity data.

    Implementations may use local SQLite, HTTP gateway, or any other backend.
    """

    async def insert(self, name: str, public_key: str) -> dict[str, str]:
        """
        Insert a new agent.

        Returns dict with keys: agent_id, name, public_key, registered_at.
        Raises DuplicateAgentError if public_key already exists.
        """
        ...

    async def get_by_id(self, agent_id: str) -> dict[str, str] | None:
        """Look up a single agent by ID. Returns None if not found."""
        ...

    async def list_all(self) -> list[dict[str, str]]:
        """List all agents (without public keys). Sorted by registered_at."""
        ...

    async def count(self) -> int:
        """Count total registered agents."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...
```

### Step 2.2: Rename AgentStore → SqliteAgentStore and make async

**File:** `services/identity/src/identity_service/services/agent_store.py`

1. Rename class `AgentStore` to `SqliteAgentStore`
2. Make all public methods async (add `async def` prefix). Since the underlying sqlite3 calls are sync, wrap them with the existing sync implementation — just add `async` keyword. This makes the class structurally compatible with `IdentityStorageInterface`.
3. Update the module docstring to "SQLite-backed agent storage (local implementation)."
4. Keep `DuplicateAgentError` in this file — it's used by both implementations.

The methods remain functionally identical — the `async` keyword is added for Protocol compatibility. The sync SQLite calls inside are fine since they're fast local disk I/O.

### Step 2.3: Update AgentRegistry to be async

**File:** `services/identity/src/identity_service/services/agent_registry.py`

1. Change the type annotation of `store` parameter from `AgentStore` to `IdentityStorageInterface`:
   ```python
   from identity_service.services.protocol import IdentityStorageInterface
   from identity_service.services.agent_store import DuplicateAgentError
   ```
2. Change `self._store` type to `IdentityStorageInterface`
3. Make these methods async (they call the store which is now async):
   - `register_agent` → `async def register_agent` (uses `await self._store.insert(...)`)
   - `get_agent` → `async def get_agent` (uses `await self._store.get_by_id(...)`)
   - `list_agents` → `async def list_agents` (uses `await self._store.list_all()`)
   - `count_agents` → `async def count_agents` (uses `await self._store.count()`)
   - `close` → `async def close` (uses `await self._store.close()`)
4. Make `verify_signature` and `verify_jws` async too — they call `self.get_agent()` which is now async:
   - `verify_signature` → `async def verify_signature` (uses `await self.get_agent(...)`)
   - `verify_jws` → `async def verify_jws` (uses `await self.get_agent(...)`)
5. Update the class docstring to remove "SQLite" mention
6. Remove import of `AgentStore` class (no longer needed, just import `DuplicateAgentError`)

### Step 2.4: Update routers to await registry calls

**File:** `services/identity/src/identity_service/routers/agents.py`

All registry calls need `await` now:
- `register_agent`: `result = await state.registry.register_agent(data["name"], data["public_key"])`
- `verify_signature`: `return await state.registry.verify_signature(...)`
- `verify_jws`: `return await state.registry.verify_jws(data["token"])`
- `list_agents`: `agents = await state.registry.list_agents()`
- `get_agent`: `agent = await state.registry.get_agent(agent_id)`

### Step 2.5: Update lifespan for async close

**File:** `services/identity/src/identity_service/core/lifespan.py`

Change:
```python
from identity_service.services.agent_store import AgentStore
```
to:
```python
from identity_service.services.agent_store import SqliteAgentStore
```

Change:
```python
store = AgentStore(db_path=db_path)
```
to:
```python
store = SqliteAgentStore(db_path=db_path)
```

Change shutdown:
```python
state.registry.close()
```
to:
```python
await state.registry.close()
```

### Step 2.6: Verify

Run from `services/identity/`:
```bash
cd services/identity && uv run pytest tests/ -x -q
```

Then full CI:
```bash
cd services/identity && just ci-quiet
```

Fix any issues. The existing tests should still pass since `SqliteAgentStore` (renamed from `AgentStore`) still implements the same interface.

---

## Tier 3: Implement GatewayAgentStore

**Goal:** Create an HTTP-based storage implementation that talks to the DB Gateway.

### Step 3.1: Create gateway store

**File:** `services/identity/src/identity_service/services/gateway_agent_store.py` (NEW)

```python
"""DB Gateway-backed agent storage."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from identity_service.logging import get_logger
from identity_service.services.agent_store import DuplicateAgentError

logger = get_logger(__name__)


class GatewayAgentStore:
    """Agent storage backed by the DB Gateway HTTP API."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def insert(self, name: str, public_key: str) -> dict[str, str]:
        """
        Insert a new agent via the DB Gateway.

        POST /identity/agents with agent data and event metadata.
        Returns dict with keys: agent_id, name, public_key, registered_at.
        Raises DuplicateAgentError if public_key already exists.
        """
        agent_id = f"a-{uuid.uuid4()}"
        registered_at = (
            datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )

        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "name": name,
            "public_key": public_key,
            "registered_at": registered_at,
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": datetime.now(UTC)
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z"),
                "agent_id": agent_id,
                "summary": f"{name} registered as agent",
                "payload": json.dumps({"agent_name": name}),
            },
        }

        response = await self._client.post("/identity/agents", json=payload)

        if response.status_code == 409:
            data = response.json()
            error_msg = data.get("message", "Public key already registered")
            raise DuplicateAgentError(error_msg)

        if response.status_code not in (200, 201):
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        return {
            "agent_id": agent_id,
            "name": name,
            "public_key": public_key,
            "registered_at": registered_at,
        }

    async def get_by_id(self, agent_id: str) -> dict[str, str] | None:
        """
        Look up a single agent by ID via the DB Gateway.

        GET /identity/agents/{agent_id}
        Returns the full agent record or None if not found.
        """
        response = await self._client.get(f"/identity/agents/{agent_id}")

        if response.status_code == 404:
            return None

        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data: dict[str, Any] = response.json()
        return {
            "agent_id": str(data["agent_id"]),
            "name": str(data["name"]),
            "public_key": str(data["public_key"]),
            "registered_at": str(data["registered_at"]),
        }

    async def list_all(self) -> list[dict[str, str]]:
        """
        List all agents via the DB Gateway.

        GET /identity/agents
        Returns list of agent summaries sorted by registration time.
        Public keys are omitted for brevity.
        """
        response = await self._client.get("/identity/agents")

        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data: dict[str, Any] = response.json()
        agents: list[dict[str, Any]] = data["agents"]
        return [
            {
                "agent_id": str(a["agent_id"]),
                "name": str(a["name"]),
                "registered_at": str(a["registered_at"]),
            }
            for a in agents
        ]

    async def count(self) -> int:
        """
        Count total registered agents via the DB Gateway.

        GET /identity/agents/count
        Returns integer count.
        """
        response = await self._client.get("/identity/agents/count")

        if response.status_code != 200:
            msg = f"Gateway error: {response.status_code} {response.text}"
            raise RuntimeError(msg)

        data: dict[str, Any] = response.json()
        return int(data["count"])

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
```

### Step 3.2: Write unit tests for GatewayAgentStore

**File:** `services/identity/tests/unit/test_gateway_agent_store.py` (NEW)

Test GatewayAgentStore by mocking httpx responses. Use `pytest` and `unittest.mock`.

```python
"""Unit tests for GatewayAgentStore."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from identity_service.services.agent_store import DuplicateAgentError
from identity_service.services.gateway_agent_store import GatewayAgentStore


def _mock_response(status_code: int, json_data: Any = None, text: str = "") -> MagicMock:
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    if json_data is not None:
        resp.json.return_value = json_data
    return resp


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
        """Duplicate public key returns 409, raises DuplicateAgentError."""
        store = GatewayAgentStore(base_url="http://localhost:8007", timeout_seconds=10)
        mock_resp = _mock_response(
            409, {"error": "public_key_exists", "message": "Already registered"}
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
```

**IMPORTANT:** The identity service needs `pytest-asyncio` for async tests. Check if it's already in `pyproject.toml`. If not, add it to the dev dependencies in `services/identity/pyproject.toml` and run `cd services/identity && uv sync --all-extras`.

### Step 3.3: Verify

Run from `services/identity/`:
```bash
cd services/identity && uv run pytest tests/ -x -q
```

Then full CI:
```bash
cd services/identity && just ci-quiet
```

---

## Tier 4: Wire GatewayAgentStore and Remove SQLite

**Goal:** The identity service uses the DB Gateway as its sole data store. Local SQLite code is removed from the active code path.

### Step 4.1: Update lifespan to use GatewayAgentStore

**File:** `services/identity/src/identity_service/core/lifespan.py`

Replace the current startup logic. The new lifespan:
1. Creates `GatewayAgentStore` using `settings.db_gateway.url` and `settings.db_gateway.timeout_seconds`
2. Creates `AgentRegistry` with the gateway store
3. Does NOT create `SqliteAgentStore` or `GatewayClient`
4. On shutdown, closes the registry (which closes the gateway store's httpx client)

```python
"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from identity_service.config import get_settings
from identity_service.core.state import init_app_state
from identity_service.logging import get_logger, setup_logging
from identity_service.services.agent_registry import AgentRegistry
from identity_service.services.gateway_agent_store import GatewayAgentStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
    logger = get_logger(__name__)

    state = init_app_state()

    # Initialize agent storage via DB Gateway
    store = GatewayAgentStore(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
    )

    # Initialize agent registry with gateway-backed store
    state.registry = AgentRegistry(
        store=store,
        algorithm=settings.crypto.algorithm,
        public_key_prefix=settings.crypto.public_key_prefix,
        public_key_bytes=settings.crypto.public_key_bytes,
        signature_bytes=settings.crypto.signature_bytes,
    )

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
    await state.registry.close()
```

### Step 4.2: Update AppState — remove gateway_client

**File:** `services/identity/src/identity_service/core/state.py`

1. Remove the `gateway_client: GatewayClient | None = None` field
2. Remove the TYPE_CHECKING import for `GatewayClient`

The state should only have:
```python
@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    registry: AgentRegistry | None = None
    ...
```

### Step 4.3: Update router — remove dual-write logic

**File:** `services/identity/src/identity_service/routers/agents.py`

In the `register_agent` endpoint, remove the entire gateway_client block:
```python
# REMOVE this entire block:
if state.gateway_client is not None:
    try:
        await state.gateway_client.register_agent(...)
    except Exception as exc:
        logger.warning(...)
```

The endpoint becomes simply:
```python
result = await state.registry.register_agent(data["name"], data["public_key"])
return JSONResponse(status_code=201, content=result)
```

### Step 4.4: Update config — make db_gateway required

**File:** `services/identity/src/identity_service/config.py`

Find the db_gateway config section. If it's currently `Optional` or has a default of `None`, make it required (no default, no Optional). The identity service MUST have a db_gateway URL configured.

Also check if `database.path` is still referenced. If the database section only served the local SQLite, it can be removed from config.py and config.yaml. However, if other tests or code reference `settings.database.path`, leave it for now and just stop using it in lifespan.

### Step 4.5: Delete gateway_client.py

**File:** `services/identity/src/identity_service/services/gateway_client.py`

Delete this file entirely. Its functionality is now handled by `GatewayAgentStore`.

### Step 4.6: Update existing test fixtures

The existing tests create an `AgentStore` (now `SqliteAgentStore`) directly. Since we cannot modify existing test files, we need to ensure the renamed class is still importable from the old location.

Check all test files in `services/identity/tests/` for imports of `AgentStore`. If any test imports `from identity_service.services.agent_store import AgentStore`, add a backward-compatible alias at the bottom of `agent_store.py`:

```python
# Backward compatibility alias for tests
AgentStore = SqliteAgentStore
```

This ensures existing tests continue to work without modification.

### Step 4.7: Verify identity service

Run from `services/identity/`:
```bash
cd services/identity && uv run pytest tests/ -x -q
```

Then full CI:
```bash
cd services/identity && just ci-quiet
```

### Step 4.8: Verify db-gateway service

Run from `services/db-gateway/`:
```bash
cd services/db-gateway && just ci-quiet
```

### Step 4.9: Full project CI

Run from project root:
```bash
just ci-all-quiet
```

This is the definitive gate. Everything must pass.

---

## Summary of Files Changed

### DB Gateway (Tier 1)
- `services/db-gateway/src/db_gateway_service/services/db_reader.py` — NEW
- `services/db-gateway/src/db_gateway_service/core/state.py` — add db_reader field
- `services/db-gateway/src/db_gateway_service/core/lifespan.py` — init db_reader
- `services/db-gateway/src/db_gateway_service/routers/identity.py` — add GET endpoints
- `services/db-gateway/tests/unit/routers/test_identity_reads.py` — NEW
- `services/db-gateway/tests/unit/test_db_reader.py` — NEW

### Identity Service (Tiers 2-4)
- `services/identity/src/identity_service/services/protocol.py` — NEW
- `services/identity/src/identity_service/services/agent_store.py` — rename class, make async
- `services/identity/src/identity_service/services/agent_registry.py` — make async, use Protocol type
- `services/identity/src/identity_service/services/gateway_agent_store.py` — NEW
- `services/identity/src/identity_service/services/gateway_client.py` — DELETE
- `services/identity/src/identity_service/core/lifespan.py` — use GatewayAgentStore
- `services/identity/src/identity_service/core/state.py` — remove gateway_client
- `services/identity/src/identity_service/routers/agents.py` — await calls, remove dual-write
- `services/identity/src/identity_service/config.py` — make db_gateway required
- `services/identity/tests/unit/test_gateway_agent_store.py` — NEW

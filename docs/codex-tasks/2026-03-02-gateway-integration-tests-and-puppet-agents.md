# Gateway Integration Tests + Offline Tests + Puppet Agent System — Complete Tiered Implementation Plan

> **Date:** 2026-03-02
>
> **Tickets covered (3 open issues):**
> - Tier 1: `agent-economy-qk0r` — Add integration tests verifying data written to economy.db
> - Tier 2: `agent-economy-nqb6` — Verify integration tests fail when DB Gateway is offline
> - Tier 3: `agent-economy-7jt5` — Implement puppet agent demo system
>
> **Dependency chain:** Tier 1 → Tier 2 (independent) + Tier 1 → Tier 3 (Tier 3 blocked by Tier 1)

## CRITICAL RULES — READ THESE BEFORE STARTING ANY WORK

1. **NO GIT.** There is no git repository in this project. Do NOT use `git add`, `git commit`, `git push`, `git status`, or ANY git commands whatsoever. Just edit files directly.
2. **Use `uv run` for ALL Python execution.** NEVER use `python`, `python3`, `pip install`, or `uv pip`. Always `uv run ...`.
3. **Do NOT modify existing test files.** If you need new tests, create new test files.
4. **Do NOT modify files in `libs/service-commons/`.** Changes there affect all services.
5. **Follow the exact file paths and code patterns.** Do not improvise or deviate from the plan.
6. **After each tier, run the specified verification commands.** Do NOT proceed to the next tier until verification passes.
7. **Work from the project root directory:** `/Users/flo/Developer/github/agent-economy`
8. **Read `AGENTS.md` FIRST** — it contains project conventions, architecture rules, and code style.
9. **All Pydantic models must use `ConfigDict(extra="forbid")`.**
10. **Never hardcode configuration values.** All config comes from `config.yaml` or environment variables.
11. **Business logic stays in `services/` layer** — routers are thin wrappers.

---

## Project Context

The Agent Task Economy has five core services (Identity, Central Bank, Task Board, Reputation, Court) that all write data through a DB Gateway service (port 8007). The DB Gateway owns a shared SQLite database (`economy.db`). Each service has a `Gateway*Store` class that makes HTTP calls to the DB Gateway instead of writing to a local database.

**Key files to read first:**
- `AGENTS.md` — Project conventions and architecture rules
- `docs/specifications/schema.sql` — The complete database schema (all tables, columns, constraints)
- `services/db-gateway/src/db_gateway_service/` — DB Gateway service code
- `services/db-gateway/tests/conftest.py` — Existing test fixtures (schema_sql, tmp_db_path, initialized_db, make_event)

**Service ports:**
- Identity: 8001
- Central Bank: 8002
- Task Board: 8003
- Reputation: 8004
- Court: 8005
- DB Gateway: 8007

---

# TIER 1: Integration Tests Verifying Data Written to economy.db

**Ticket:** `agent-economy-qk0r`

## Goal

Write integration tests that verify the full data flow: HTTP request → service → Gateway*Store → DB Gateway HTTP API → economy.db. Each test makes an API call to a service (configured to use the DB Gateway), then reads economy.db directly with SQLite to verify the data was written correctly.

## Test Architecture

Each test module:
1. Creates a temporary `economy.db` initialized from `docs/specifications/schema.sql`
2. Starts the DB Gateway in-process using `httpx.ASGITransport`
3. Starts the service under test in-process, configured to talk to the in-process DB Gateway
4. Makes API calls to the service
5. Opens economy.db with `sqlite3` and SELECTs to verify data was written correctly

**Tests live at:** `tests/integration/` (project root level — these are cross-service tests)

## Step 1: Create the test infrastructure

### File: `tests/__init__.py`

Create an empty `__init__.py`:

```python
```

### File: `tests/integration/__init__.py`

Create an empty `__init__.py`:

```python
```

### File: `tests/integration/conftest.py`

This provides shared fixtures for all integration test modules. The key pattern: both the DB Gateway and the service under test run in the same process using `httpx.ASGITransport`. The service's Gateway store is configured to point to the in-process DB Gateway.

```python
"""Cross-service integration test fixtures.

Each test gets a fresh economy.db (temp file) initialized from schema.sql.
The DB Gateway runs in-process via ASGITransport.
Services under test are configured to talk to the in-process DB Gateway.
"""
from __future__ import annotations

import contextlib
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


SCHEMA_PATH = Path(__file__).parent.parent.parent / "docs" / "specifications" / "schema.sql"


@pytest.fixture
def schema_sql() -> str:
    """Load the shared economy.db schema."""
    return SCHEMA_PATH.read_text()


@pytest.fixture
def tmp_db_path() -> Iterator[str]:
    """Create a temporary database file path, cleaned up after test."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    with contextlib.suppress(OSError):
        Path(path).unlink()


@pytest.fixture
def initialized_db(tmp_db_path: str, schema_sql: str) -> str:
    """Create a temporary database with the full schema initialized."""
    conn = sqlite3.connect(tmp_db_path)
    conn.executescript(schema_sql)
    conn.close()
    return tmp_db_path


def read_db(db_path: str, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """Execute a SELECT query and return results as list of dicts."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows


def read_one(db_path: str, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    """Execute a SELECT query and return the first result as a dict, or None."""
    rows = read_db(db_path, query, params)
    return rows[0] if rows else None


def count_rows(db_path: str, table: str) -> int:
    """Count rows in a table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")  # noqa: S608
    result = cursor.fetchone()[0]
    conn.close()
    return int(result)


def make_event(
    source: str,
    event_type: str,
    summary: str,
    agent_id: str | None = None,
    task_id: str | None = None,
    payload: str = "{}",
) -> dict[str, Any]:
    """Construct a valid event dict for DB Gateway payloads."""
    return {
        "event_source": source,
        "event_type": event_type,
        "timestamp": "2026-03-02T00:00:00Z",
        "agent_id": agent_id,
        "task_id": task_id,
        "summary": summary,
        "payload": payload,
    }
```

### File: `tests/integration/gateway_helpers.py`

Shared helper to create an in-process DB Gateway test client.

```python
"""Helpers to create in-process DB Gateway for integration tests."""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import httpx


def create_gateway_client(db_path: str) -> httpx.AsyncClient:
    """Create an httpx.AsyncClient that talks to an in-process DB Gateway.

    The DB Gateway app is created with the database pointing at db_path.
    Returns an async client using ASGITransport — no real HTTP server needed.
    """
    # Set env vars BEFORE importing the app factory to configure the DB path
    env_overrides = {
        "DB_GATEWAY_DATABASE__PATH": db_path,
        "DB_GATEWAY_DATABASE__SCHEMA_PATH": str(
            __import__("pathlib").Path(__file__).parent.parent.parent
            / "docs"
            / "specifications"
            / "schema.sql"
        ),
    }

    with patch.dict(os.environ, env_overrides):
        # Clear any cached settings from previous tests
        from db_gateway_service.config import clear_settings_cache

        clear_settings_cache()

        from db_gateway_service.app import create_app

        app = create_app()

    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url="http://gateway-test")


async def post_json(
    client: httpx.AsyncClient, path: str, payload: dict[str, Any]
) -> httpx.Response:
    """POST JSON to the gateway and return the response."""
    return await client.post(path, json=payload)


async def get_json(client: httpx.AsyncClient, path: str) -> httpx.Response:
    """GET from the gateway and return the response."""
    return await client.get(path)
```

## Step 2: Identity Integration Tests

### File: `tests/integration/test_identity_gateway_writes.py`

```python
"""Integration tests: Identity service → DB Gateway → economy.db.

Verifies that agent registration through the Identity service correctly
writes data to the identity_agents table and the events table.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tests.integration.conftest import count_rows, read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration]


@pytest.fixture
async def gw_client(initialized_db: str):
    """Create an in-process DB Gateway client."""
    client = create_gateway_client(initialized_db)
    async with client:
        yield client


class TestIdentityGatewayWrites:
    """Verify agent data flows to economy.db correctly."""

    async def test_register_agent_creates_row_in_identity_agents(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        """POST /identity/agents → row in identity_agents table."""
        payload = {
            "agent_id": "a-test-001",
            "name": "Alice",
            "public_key": "ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "registered_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": "a-test-001",
                "summary": "Alice registered as agent",
                "payload": json.dumps({"agent_name": "Alice"}),
            },
        }
        resp = await gw_client.post("/identity/agents", json=payload)
        assert resp.status_code == 201

        # Verify row in identity_agents
        row = read_one(initialized_db, "SELECT * FROM identity_agents WHERE agent_id = ?", ("a-test-001",))
        assert row is not None
        assert row["name"] == "Alice"
        assert row["public_key"] == "ed25519:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        assert row["registered_at"] == "2026-03-02T00:00:00Z"

    async def test_register_agent_creates_event(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        """POST /identity/agents → row in events table."""
        payload = {
            "agent_id": "a-test-002",
            "name": "Bob",
            "public_key": "ed25519:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            "registered_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": "a-test-002",
                "summary": "Bob registered as agent",
                "payload": json.dumps({"agent_name": "Bob"}),
            },
        }
        resp = await gw_client.post("/identity/agents", json=payload)
        assert resp.status_code == 201

        # Verify event was logged
        row = read_one(initialized_db, "SELECT * FROM events WHERE agent_id = ?", ("a-test-002",))
        assert row is not None
        assert row["event_source"] == "identity"
        assert row["event_type"] == "agent.registered"
        assert "Bob" in row["summary"]

    async def test_register_duplicate_public_key_returns_409(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        """POST /identity/agents with duplicate key → 409, no extra row."""
        payload = {
            "agent_id": "a-test-003",
            "name": "Carol",
            "public_key": "ed25519:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            "registered_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": "a-test-003",
                "summary": "Carol registered as agent",
                "payload": json.dumps({"agent_name": "Carol"}),
            },
        }
        resp1 = await gw_client.post("/identity/agents", json=payload)
        assert resp1.status_code == 201

        # Same public key, different agent_id
        payload2 = {**payload, "agent_id": "a-test-003b", "name": "Carol Duplicate"}
        payload2["event"] = {**payload["event"], "agent_id": "a-test-003b"}
        resp2 = await gw_client.post("/identity/agents", json=payload2)
        assert resp2.status_code == 409

        # Only one row should exist
        assert count_rows(initialized_db, "identity_agents") == 1

    async def test_register_agent_fields_map_correctly(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        """Verify all fields in the response match what's in the database."""
        payload = {
            "agent_id": "a-test-004",
            "name": "Dave the Agent",
            "public_key": "ed25519:DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
            "registered_at": "2026-03-02T00:40:37Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-02T00:40:37Z",
                "agent_id": "a-test-004",
                "summary": "Dave the Agent registered",
                "payload": json.dumps({"agent_name": "Dave the Agent"}),
            },
        }
        resp = await gw_client.post("/identity/agents", json=payload)
        assert resp.status_code == 201

        row = read_one(initialized_db, "SELECT * FROM identity_agents WHERE agent_id = ?", ("a-test-004",))
        assert row is not None
        assert row["agent_id"] == "a-test-004"
        assert row["name"] == "Dave the Agent"
        assert row["public_key"] == "ed25519:DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD="
        assert row["registered_at"] == "2026-03-02T00:40:37Z"
```

**IMPORTANT:** The above is a template. The actual test file needs the correct import at the top:

```python
import httpx
```

## Step 3: Central Bank Integration Tests

### File: `tests/integration/test_bank_gateway_writes.py`

```python
"""Integration tests: Central Bank operations → DB Gateway → economy.db.

Verifies account creation, credits, escrow lock/release/split all write
correct data to bank_accounts, bank_transactions, and bank_escrow tables.
"""
from __future__ import annotations

import json

import httpx
import pytest

from tests.integration.conftest import count_rows, read_db, read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration]


def _register_agent(agent_id: str, name: str) -> dict:
    """Build a payload to register an agent (prerequisite for bank operations)."""
    return {
        "agent_id": agent_id,
        "name": name,
        "public_key": f"ed25519:{agent_id.replace('a-', '').upper().ljust(43, 'A')}=",
        "registered_at": "2026-03-02T00:00:00Z",
        "event": {
            "event_source": "identity",
            "event_type": "agent.registered",
            "timestamp": "2026-03-02T00:00:00Z",
            "agent_id": agent_id,
            "summary": f"{name} registered",
            "payload": json.dumps({"agent_name": name}),
        },
    }


@pytest.fixture
async def gw_client(initialized_db: str):
    """Create an in-process DB Gateway client with a pre-registered agent."""
    client = create_gateway_client(initialized_db)
    async with client:
        # Pre-register an agent (foreign key requirement for bank_accounts)
        resp = await client.post("/identity/agents", json=_register_agent("a-alice", "Alice"))
        assert resp.status_code == 201
        resp = await client.post("/identity/agents", json=_register_agent("a-bob", "Bob"))
        assert resp.status_code == 201
        yield client


class TestBankAccountCreation:
    """Verify account creation writes to bank_accounts table."""

    async def test_create_account_writes_row(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        payload = {
            "account_id": "a-alice",
            "balance": 0,
            "created_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": "a-alice",
                "summary": "Account created for Alice",
                "payload": json.dumps({"agent_name": "Alice"}),
            },
        }
        resp = await gw_client.post("/bank/accounts", json=payload)
        assert resp.status_code == 201

        row = read_one(initialized_db, "SELECT * FROM bank_accounts WHERE account_id = ?", ("a-alice",))
        assert row is not None
        assert row["account_id"] == "a-alice"
        assert row["balance"] == 0
        assert row["created_at"] == "2026-03-02T00:00:00Z"

    async def test_create_account_with_initial_credit(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        payload = {
            "account_id": "a-alice",
            "balance": 1000,
            "created_at": "2026-03-02T00:00:00Z",
            "initial_credit": {
                "tx_id": "tx-init-001",
                "amount": 1000,
                "reference": "initial_balance",
                "timestamp": "2026-03-02T00:00:00Z",
            },
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": "a-alice",
                "summary": "Account created for Alice",
                "payload": json.dumps({"agent_name": "Alice"}),
            },
        }
        resp = await gw_client.post("/bank/accounts", json=payload)
        assert resp.status_code == 201

        row = read_one(initialized_db, "SELECT * FROM bank_accounts WHERE account_id = ?", ("a-alice",))
        assert row is not None
        assert row["balance"] == 1000

        # Verify the initial credit transaction was recorded
        tx = read_one(initialized_db, "SELECT * FROM bank_transactions WHERE tx_id = ?", ("tx-init-001",))
        assert tx is not None
        assert tx["type"] == "credit"
        assert tx["amount"] == 1000
        assert tx["balance_after"] == 1000


class TestBankCredit:
    """Verify credit operations write to bank_transactions."""

    async def test_credit_writes_transaction_and_updates_balance(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        # First create account
        await gw_client.post("/bank/accounts", json={
            "account_id": "a-alice",
            "balance": 0,
            "created_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": "a-alice",
                "summary": "Account created",
                "payload": "{}",
            },
        })

        # Now credit
        resp = await gw_client.post("/bank/credit", json={
            "tx_id": "tx-credit-001",
            "account_id": "a-alice",
            "amount": 500,
            "reference": "salary_round_1",
            "timestamp": "2026-03-02T00:00:16Z",
            "event": {
                "event_source": "bank",
                "event_type": "salary.paid",
                "timestamp": "2026-03-02T00:00:16Z",
                "agent_id": "a-alice",
                "summary": "Credited 500 to Alice",
                "payload": json.dumps({"amount": 500}),
            },
        })
        assert resp.status_code in (200, 201)

        # Verify transaction row
        tx = read_one(initialized_db, "SELECT * FROM bank_transactions WHERE tx_id = ?", ("tx-credit-001",))
        assert tx is not None
        assert tx["type"] == "credit"
        assert tx["amount"] == 500
        assert tx["balance_after"] == 500
        assert tx["reference"] == "salary_round_1"

        # Verify balance updated
        acct = read_one(initialized_db, "SELECT * FROM bank_accounts WHERE account_id = ?", ("a-alice",))
        assert acct is not None
        assert acct["balance"] == 500


class TestBankEscrow:
    """Verify escrow lock/release/split operations."""

    async def _setup_funded_account(
        self, client: httpx.AsyncClient, agent_id: str, balance: int
    ) -> None:
        """Create account and fund it."""
        await client.post("/bank/accounts", json={
            "account_id": agent_id,
            "balance": balance,
            "created_at": "2026-03-02T00:00:00Z",
            "initial_credit": {
                "tx_id": f"tx-init-{agent_id}",
                "amount": balance,
                "reference": "initial_balance",
                "timestamp": "2026-03-02T00:00:00Z",
            },
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": agent_id,
                "summary": f"Account created for {agent_id}",
                "payload": json.dumps({"agent_name": agent_id}),
            },
        })

    async def test_escrow_lock_writes_escrow_row_and_debit_tx(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        await self._setup_funded_account(gw_client, "a-alice", 1000)

        resp = await gw_client.post("/bank/escrow/lock", json={
            "escrow_id": "esc-001",
            "payer_account_id": "a-alice",
            "amount": 200,
            "task_id": "t-task-001",
            "created_at": "2026-03-02T00:00:32Z",
            "tx_id": "tx-escrow-lock-001",
            "event": {
                "event_source": "bank",
                "event_type": "escrow.locked",
                "timestamp": "2026-03-02T00:00:32Z",
                "agent_id": "a-alice",
                "task_id": "t-task-001",
                "summary": "Escrow locked: 200 for task t-task-001",
                "payload": json.dumps({"escrow_id": "esc-001", "amount": 200, "title": "t-task-001"}),
            },
        })
        assert resp.status_code in (200, 201)

        # Verify escrow row
        esc = read_one(initialized_db, "SELECT * FROM bank_escrow WHERE escrow_id = ?", ("esc-001",))
        assert esc is not None
        assert esc["payer_account_id"] == "a-alice"
        assert esc["amount"] == 200
        assert esc["task_id"] == "t-task-001"
        assert esc["status"] == "locked"

        # Verify balance was debited
        acct = read_one(initialized_db, "SELECT * FROM bank_accounts WHERE account_id = ?", ("a-alice",))
        assert acct is not None
        assert acct["balance"] == 800  # 1000 - 200

        # Verify escrow_lock transaction
        tx = read_one(initialized_db, "SELECT * FROM bank_transactions WHERE tx_id = ?", ("tx-escrow-lock-001",))
        assert tx is not None
        assert tx["type"] == "escrow_lock"
        assert tx["amount"] == 200

    async def test_escrow_release_updates_status_and_credits_recipient(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        await self._setup_funded_account(gw_client, "a-alice", 1000)
        await self._setup_funded_account(gw_client, "a-bob", 0)

        # Lock escrow
        await gw_client.post("/bank/escrow/lock", json={
            "escrow_id": "esc-002",
            "payer_account_id": "a-alice",
            "amount": 300,
            "task_id": "t-task-002",
            "created_at": "2026-03-02T00:00:48Z",
            "tx_id": "tx-escrow-lock-002",
            "event": {
                "event_source": "bank",
                "event_type": "escrow.locked",
                "timestamp": "2026-03-02T00:00:48Z",
                "agent_id": "a-alice",
                "task_id": "t-task-002",
                "summary": "Escrow locked",
                "payload": json.dumps({"escrow_id": "esc-002", "amount": 300}),
            },
        })

        # Release escrow
        resp = await gw_client.post("/bank/escrow/release", json={
            "escrow_id": "esc-002",
            "recipient_account_id": "a-bob",
            "tx_id": "tx-escrow-release-002",
            "resolved_at": "2026-03-02T00:01:05Z",
            "constraints": {"status": "locked"},
            "event": {
                "event_source": "bank",
                "event_type": "escrow.released",
                "timestamp": "2026-03-02T00:01:05Z",
                "summary": "Escrow released to Bob",
                "payload": json.dumps({"escrow_id": "esc-002", "recipient_id": "a-bob"}),
            },
        })
        assert resp.status_code in (200, 201)

        # Verify escrow status changed
        esc = read_one(initialized_db, "SELECT * FROM bank_escrow WHERE escrow_id = ?", ("esc-002",))
        assert esc is not None
        assert esc["status"] == "released"
        assert esc["resolved_at"] == "2026-03-02T00:01:05Z"

        # Verify recipient got credited
        bob = read_one(initialized_db, "SELECT * FROM bank_accounts WHERE account_id = ?", ("a-bob",))
        assert bob is not None
        assert bob["balance"] == 300

    async def test_escrow_split_creates_two_transactions(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        await self._setup_funded_account(gw_client, "a-alice", 1000)
        await self._setup_funded_account(gw_client, "a-bob", 0)

        # Lock escrow
        await gw_client.post("/bank/escrow/lock", json={
            "escrow_id": "esc-003",
            "payer_account_id": "a-alice",
            "amount": 100,
            "task_id": "t-task-003",
            "created_at": "2026-03-02T00:01:21Z",
            "tx_id": "tx-escrow-lock-003",
            "event": {
                "event_source": "bank",
                "event_type": "escrow.locked",
                "timestamp": "2026-03-02T00:01:21Z",
                "agent_id": "a-alice",
                "task_id": "t-task-003",
                "summary": "Escrow locked",
                "payload": json.dumps({"escrow_id": "esc-003", "amount": 100}),
            },
        })

        # Split: 70% worker, 30% poster
        resp = await gw_client.post("/bank/escrow/split", json={
            "escrow_id": "esc-003",
            "worker_account_id": "a-bob",
            "poster_account_id": "a-alice",
            "worker_amount": 70,
            "poster_amount": 30,
            "worker_tx_id": "tx-split-worker-003",
            "poster_tx_id": "tx-split-poster-003",
            "resolved_at": "2026-03-02T00:01:37Z",
            "constraints": {"status": "locked"},
            "event": {
                "event_source": "bank",
                "event_type": "escrow.split",
                "timestamp": "2026-03-02T00:01:37Z",
                "summary": "Escrow split",
                "payload": json.dumps({"escrow_id": "esc-003", "worker_amount": 70, "poster_amount": 30}),
            },
        })
        assert resp.status_code in (200, 201)

        # Verify escrow resolved
        esc = read_one(initialized_db, "SELECT * FROM bank_escrow WHERE escrow_id = ?", ("esc-003",))
        assert esc is not None
        assert esc["status"] == "split"

        # Verify worker transaction
        worker_tx = read_one(initialized_db, "SELECT * FROM bank_transactions WHERE tx_id = ?", ("tx-split-worker-003",))
        assert worker_tx is not None
        assert worker_tx["account_id"] == "a-bob"
        assert worker_tx["amount"] == 70
        assert worker_tx["type"] == "escrow_release"

        # Verify poster refund transaction
        poster_tx = read_one(initialized_db, "SELECT * FROM bank_transactions WHERE tx_id = ?", ("tx-split-poster-003",))
        assert poster_tx is not None
        assert poster_tx["account_id"] == "a-alice"
        assert poster_tx["amount"] == 30
        assert poster_tx["type"] == "escrow_release"


class TestBankEvents:
    """Verify bank operations generate events in the events table."""

    async def test_account_creation_generates_event(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        await gw_client.post("/bank/accounts", json={
            "account_id": "a-alice",
            "balance": 0,
            "created_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": "a-alice",
                "summary": "Account created for Alice",
                "payload": json.dumps({"agent_name": "Alice"}),
            },
        })

        events = read_db(initialized_db, "SELECT * FROM events WHERE event_source = 'bank'")
        assert len(events) >= 1
        assert any(e["event_type"] == "account.created" for e in events)
```

## Step 4: Task Board Integration Tests

### File: `tests/integration/test_taskboard_gateway_writes.py`

```python
"""Integration tests: Task Board operations → DB Gateway → economy.db.

Verifies task creation, bidding, status updates, and asset recording.
"""
from __future__ import annotations

import json

import httpx
import pytest

from tests.integration.conftest import count_rows, read_db, read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration]


async def _setup_agents_and_accounts(client: httpx.AsyncClient) -> None:
    """Register agents and create funded accounts."""
    for agent_id, name in [("a-poster", "Poster"), ("a-worker", "Worker")]:
        await client.post("/identity/agents", json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{agent_id.replace('a-', '').upper().ljust(43, 'X')}=",
            "registered_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": agent_id,
                "summary": f"{name} registered",
                "payload": json.dumps({"agent_name": name}),
            },
        })
        await client.post("/bank/accounts", json={
            "account_id": agent_id,
            "balance": 5000,
            "created_at": "2026-03-02T00:00:00Z",
            "initial_credit": {
                "tx_id": f"tx-init-{agent_id}",
                "amount": 5000,
                "reference": "initial_balance",
                "timestamp": "2026-03-02T00:00:00Z",
            },
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": agent_id,
                "summary": f"Account created for {name}",
                "payload": json.dumps({"agent_name": name}),
            },
        })


async def _create_task_with_escrow(
    client: httpx.AsyncClient, task_id: str, poster_id: str, reward: int
) -> None:
    """Create escrow + task (both required for board_tasks FK)."""
    # Lock escrow first
    await client.post("/bank/escrow/lock", json={
        "escrow_id": f"esc-{task_id}",
        "payer_account_id": poster_id,
        "amount": reward,
        "task_id": task_id,
        "created_at": "2026-03-02T00:00:16Z",
        "tx_id": f"tx-escrow-{task_id}",
        "event": {
            "event_source": "bank",
            "event_type": "escrow.locked",
            "timestamp": "2026-03-02T00:00:16Z",
            "agent_id": poster_id,
            "task_id": task_id,
            "summary": f"Escrow locked for {task_id}",
            "payload": json.dumps({"escrow_id": f"esc-{task_id}", "amount": reward}),
        },
    })

    # Create task
    await client.post("/board/tasks", json={
        "task_id": task_id,
        "poster_id": poster_id,
        "title": "Test Task",
        "spec": "Implement something useful",
        "reward": reward,
        "status": "open",
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 7200,
        "review_deadline_seconds": 3600,
        "bidding_deadline": "2026-03-02T00:16:15Z",
        "bid_count": 0,
        "escrow_pending": 0,
        "escrow_id": f"esc-{task_id}",
        "created_at": "2026-03-02T00:00:16Z",
        "event": {
            "event_source": "board",
            "event_type": "task.created",
            "timestamp": "2026-03-02T00:00:16Z",
            "agent_id": poster_id,
            "task_id": task_id,
            "summary": "Task created: Test Task",
            "payload": json.dumps({"title": "Test Task", "reward": reward}),
        },
    })


@pytest.fixture
async def gw_client(initialized_db: str):
    client = create_gateway_client(initialized_db)
    async with client:
        await _setup_agents_and_accounts(client)
        yield client


class TestTaskBoardTaskCreation:

    async def test_create_task_writes_row(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        await _create_task_with_escrow(gw_client, "t-test-001", "a-poster", 200)

        row = read_one(initialized_db, "SELECT * FROM board_tasks WHERE task_id = ?", ("t-test-001",))
        assert row is not None
        assert row["poster_id"] == "a-poster"
        assert row["title"] == "Test Task"
        assert row["spec"] == "Implement something useful"
        assert row["reward"] == 200
        assert row["status"] == "open"
        assert row["escrow_id"] == "esc-t-test-001"


class TestTaskBoardBidding:

    async def test_submit_bid_writes_row_and_increments_bid_count(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        await _create_task_with_escrow(gw_client, "t-bid-001", "a-poster", 200)

        resp = await gw_client.post("/board/bids", json={
            "bid_id": "bid-001",
            "task_id": "t-bid-001",
            "bidder_id": "a-worker",
            "proposal": "I can do this well",
            "amount": 180,
            "submitted_at": "2026-03-02T00:01:21Z",
            "event": {
                "event_source": "board",
                "event_type": "bid.submitted",
                "timestamp": "2026-03-02T00:01:21Z",
                "agent_id": "a-worker",
                "task_id": "t-bid-001",
                "summary": "Bid submitted on Test Task",
                "payload": json.dumps({"bid_id": "bid-001", "title": "Test Task", "bid_count": 1}),
            },
        })
        assert resp.status_code in (200, 201)

        bid = read_one(initialized_db, "SELECT * FROM board_bids WHERE bid_id = ?", ("bid-001",))
        assert bid is not None
        assert bid["task_id"] == "t-bid-001"
        assert bid["bidder_id"] == "a-worker"
        assert bid["amount"] == 180
        assert bid["proposal"] == "I can do this well"

        # Verify bid_count incremented on task
        task = read_one(initialized_db, "SELECT * FROM board_tasks WHERE task_id = ?", ("t-bid-001",))
        assert task is not None
        assert task["bid_count"] == 1


class TestTaskBoardStatusUpdates:

    async def test_update_task_status(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        await _create_task_with_escrow(gw_client, "t-status-001", "a-poster", 200)

        # Update task to accepted
        resp = await gw_client.post("/board/tasks/t-status-001", json={
            "updates": {
                "status": "accepted",
                "worker_id": "a-worker",
                "accepted_bid_id": "bid-accepted-001",
                "accepted_at": "2026-03-02T00:02:42Z",
                "execution_deadline": "2026-03-02T00:35:12Z",
            },
            "constraints": {"status": "open"},
            "event": {
                "event_source": "board",
                "event_type": "task.accepted",
                "timestamp": "2026-03-02T00:02:42Z",
                "agent_id": "a-poster",
                "task_id": "t-status-001",
                "summary": "Task accepted",
                "payload": json.dumps({"title": "Test Task", "worker_id": "a-worker"}),
            },
        })
        assert resp.status_code in (200, 201)

        task = read_one(initialized_db, "SELECT * FROM board_tasks WHERE task_id = ?", ("t-status-001",))
        assert task is not None
        assert task["status"] == "accepted"
        assert task["worker_id"] == "a-worker"


class TestTaskBoardAssets:

    async def test_record_asset_writes_row(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        await _create_task_with_escrow(gw_client, "t-asset-001", "a-poster", 200)

        resp = await gw_client.post("/board/assets", json={
            "asset_id": "asset-001",
            "task_id": "t-asset-001",
            "uploader_id": "a-worker",
            "filename": "deliverable.zip",
            "content_type": "application/zip",
            "size_bytes": 1024,
            "storage_path": "/data/assets/asset-001",
            "content_hash": "sha256:abc123",
            "uploaded_at": "2026-03-02T00:04:03Z",
            "event": {
                "event_source": "board",
                "event_type": "asset.uploaded",
                "timestamp": "2026-03-02T00:04:03Z",
                "agent_id": "a-worker",
                "task_id": "t-asset-001",
                "summary": "Asset uploaded: deliverable.zip",
                "payload": json.dumps({"title": "Test Task", "filename": "deliverable.zip", "size_bytes": 1024}),
            },
        })
        assert resp.status_code in (200, 201)

        asset = read_one(initialized_db, "SELECT * FROM board_assets WHERE asset_id = ?", ("asset-001",))
        assert asset is not None
        assert asset["filename"] == "deliverable.zip"
        assert asset["content_type"] == "application/zip"
        assert asset["size_bytes"] == 1024
        assert asset["storage_path"] == "/data/assets/asset-001"
```

## Step 5: Reputation Integration Tests

### File: `tests/integration/test_reputation_gateway_writes.py`

```python
"""Integration tests: Reputation operations → DB Gateway → economy.db.

Verifies feedback submission and reveal update the reputation_feedback table.
"""
from __future__ import annotations

import json

import httpx
import pytest

from tests.integration.conftest import read_db, read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration]


async def _setup_prerequisite_data(client: httpx.AsyncClient) -> str:
    """Register agents, create accounts, create a task. Returns task_id."""
    for agent_id, name in [("a-poster", "Poster"), ("a-worker", "Worker")]:
        await client.post("/identity/agents", json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{agent_id.replace('a-', '').upper().ljust(43, 'Z')}=",
            "registered_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": agent_id,
                "summary": f"{name} registered",
                "payload": json.dumps({"agent_name": name}),
            },
        })
        await client.post("/bank/accounts", json={
            "account_id": agent_id,
            "balance": 5000,
            "created_at": "2026-03-02T00:00:00Z",
            "initial_credit": {
                "tx_id": f"tx-init-{agent_id}",
                "amount": 5000,
                "reference": "initial_balance",
                "timestamp": "2026-03-02T00:00:00Z",
            },
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": agent_id,
                "summary": f"Account for {name}",
                "payload": "{}",
            },
        })

    # Create escrow + task
    task_id = "t-rep-001"
    await client.post("/bank/escrow/lock", json={
        "escrow_id": f"esc-{task_id}",
        "payer_account_id": "a-poster",
        "amount": 200,
        "task_id": task_id,
        "created_at": "2026-03-02T00:00:16Z",
        "tx_id": f"tx-esc-{task_id}",
        "event": {
            "event_source": "bank",
            "event_type": "escrow.locked",
            "timestamp": "2026-03-02T00:00:16Z",
            "agent_id": "a-poster",
            "task_id": task_id,
            "summary": "Escrow locked",
            "payload": "{}",
        },
    })
    await client.post("/board/tasks", json={
        "task_id": task_id,
        "poster_id": "a-poster",
        "title": "Task for feedback",
        "spec": "Do something",
        "reward": 200,
        "status": "approved",
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 7200,
        "review_deadline_seconds": 3600,
        "bidding_deadline": "2026-03-02T00:16:15Z",
        "bid_count": 0,
        "escrow_pending": 0,
        "escrow_id": f"esc-{task_id}",
        "created_at": "2026-03-02T00:00:16Z",
        "event": {
            "event_source": "board",
            "event_type": "task.created",
            "timestamp": "2026-03-02T00:00:16Z",
            "agent_id": "a-poster",
            "task_id": task_id,
            "summary": "Task created",
            "payload": "{}",
        },
    })
    return task_id


@pytest.fixture
async def gw_client(initialized_db: str):
    client = create_gateway_client(initialized_db)
    async with client:
        yield client


class TestReputationFeedback:

    async def test_submit_feedback_writes_sealed_row(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        task_id = await _setup_prerequisite_data(gw_client)

        resp = await gw_client.post("/reputation/feedback", json={
            "feedback_id": "fb-001",
            "task_id": task_id,
            "from_agent_id": "a-poster",
            "to_agent_id": "a-worker",
            "role": "poster",
            "category": "delivery_quality",
            "rating": "satisfied",
            "comment": "Good work",
            "submitted_at": "2026-03-02T00:05:25Z",
            "visible": 0,
            "event": {
                "event_source": "reputation",
                "event_type": "feedback.submitted",
                "timestamp": "2026-03-02T00:05:25Z",
                "agent_id": "a-poster",
                "task_id": task_id,
                "summary": "Feedback submitted by Poster",
                "payload": json.dumps({"from_name": "Poster", "to_name": "Worker"}),
            },
        })
        assert resp.status_code in (200, 201)

        row = read_one(initialized_db, "SELECT * FROM reputation_feedback WHERE feedback_id = ?", ("fb-001",))
        assert row is not None
        assert row["task_id"] == task_id
        assert row["from_agent_id"] == "a-poster"
        assert row["to_agent_id"] == "a-worker"
        assert row["role"] == "poster"
        assert row["category"] == "delivery_quality"
        assert row["rating"] == "satisfied"
        assert row["comment"] == "Good work"
        assert row["visible"] == 0  # sealed

    async def test_reveal_feedback_sets_visible_true(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        task_id = await _setup_prerequisite_data(gw_client)

        # Submit both feedbacks (both directions)
        for fb_id, from_id, to_id, role, cat in [
            ("fb-r1", "a-poster", "a-worker", "poster", "delivery_quality"),
            ("fb-r2", "a-worker", "a-poster", "worker", "spec_quality"),
        ]:
            await gw_client.post("/reputation/feedback", json={
                "feedback_id": fb_id,
                "task_id": task_id,
                "from_agent_id": from_id,
                "to_agent_id": to_id,
                "role": role,
                "category": cat,
                "rating": "satisfied",
                "comment": "Good",
                "submitted_at": "2026-03-02T00:06:46Z",
                "visible": 0,
                "event": {
                    "event_source": "reputation",
                    "event_type": "feedback.submitted",
                    "timestamp": "2026-03-02T00:06:46Z",
                    "agent_id": from_id,
                    "task_id": task_id,
                    "summary": "Feedback submitted",
                    "payload": "{}",
                },
            })

        # Reveal (sets visible=1 for both feedbacks of this task)
        resp = await gw_client.post("/reputation/reveal", json={
            "task_id": task_id,
            "event": {
                "event_source": "reputation",
                "event_type": "feedback.revealed",
                "timestamp": "2026-03-02T00:07:02Z",
                "task_id": task_id,
                "summary": "Feedback revealed for task",
                "payload": json.dumps({"task_id": task_id}),
            },
        })
        assert resp.status_code in (200, 201)

        rows = read_db(initialized_db, "SELECT * FROM reputation_feedback WHERE task_id = ?", (task_id,))
        assert len(rows) == 2
        assert all(r["visible"] == 1 for r in rows)
```

## Step 6: Court Integration Tests

### File: `tests/integration/test_court_gateway_writes.py`

```python
"""Integration tests: Court operations → DB Gateway → economy.db.

Verifies claim filing, rebuttal submission, and ruling recording.
"""
from __future__ import annotations

import json

import httpx
import pytest

from tests.integration.conftest import read_one
from tests.integration.gateway_helpers import create_gateway_client

pytestmark = [pytest.mark.integration]


async def _setup_prerequisite_data(client: httpx.AsyncClient) -> str:
    """Register agents, create accounts, create disputed task. Returns task_id."""
    for agent_id, name in [("a-claimant", "Claimant"), ("a-respondent", "Respondent")]:
        await client.post("/identity/agents", json={
            "agent_id": agent_id,
            "name": name,
            "public_key": f"ed25519:{agent_id.replace('a-', '').upper().ljust(43, 'Q')}=",
            "registered_at": "2026-03-02T00:00:00Z",
            "event": {
                "event_source": "identity",
                "event_type": "agent.registered",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": agent_id,
                "summary": f"{name} registered",
                "payload": json.dumps({"agent_name": name}),
            },
        })
        await client.post("/bank/accounts", json={
            "account_id": agent_id,
            "balance": 5000,
            "created_at": "2026-03-02T00:00:00Z",
            "initial_credit": {
                "tx_id": f"tx-init-{agent_id}",
                "amount": 5000,
                "reference": "initial_balance",
                "timestamp": "2026-03-02T00:00:00Z",
            },
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": "2026-03-02T00:00:00Z",
                "agent_id": agent_id,
                "summary": f"Account for {name}",
                "payload": "{}",
            },
        })

    task_id = "t-court-001"
    await client.post("/bank/escrow/lock", json={
        "escrow_id": f"esc-{task_id}",
        "payer_account_id": "a-claimant",
        "amount": 200,
        "task_id": task_id,
        "created_at": "2026-03-02T00:00:16Z",
        "tx_id": f"tx-esc-{task_id}",
        "event": {
            "event_source": "bank",
            "event_type": "escrow.locked",
            "timestamp": "2026-03-02T00:00:16Z",
            "agent_id": "a-claimant",
            "task_id": task_id,
            "summary": "Escrow locked",
            "payload": "{}",
        },
    })
    await client.post("/board/tasks", json={
        "task_id": task_id,
        "poster_id": "a-claimant",
        "title": "Disputed Task",
        "spec": "Build something",
        "reward": 200,
        "status": "disputed",
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 7200,
        "review_deadline_seconds": 3600,
        "bidding_deadline": "2026-03-02T00:16:15Z",
        "bid_count": 1,
        "escrow_pending": 0,
        "escrow_id": f"esc-{task_id}",
        "worker_id": "a-respondent",
        "created_at": "2026-03-02T00:00:16Z",
        "event": {
            "event_source": "board",
            "event_type": "task.created",
            "timestamp": "2026-03-02T00:00:16Z",
            "agent_id": "a-claimant",
            "task_id": task_id,
            "summary": "Task created",
            "payload": "{}",
        },
    })
    return task_id


@pytest.fixture
async def gw_client(initialized_db: str):
    client = create_gateway_client(initialized_db)
    async with client:
        yield client


class TestCourtClaims:

    async def test_file_claim_writes_row(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        task_id = await _setup_prerequisite_data(gw_client)

        resp = await gw_client.post("/court/claims", json={
            "claim_id": "clm-001",
            "task_id": task_id,
            "claimant_id": "a-claimant",
            "respondent_id": "a-respondent",
            "reason": "Work does not meet specification",
            "status": "filed",
            "rebuttal_deadline": "2026-03-02T06:30:00Z",
            "filed_at": "2026-03-02T00:08:07Z",
            "event": {
                "event_source": "court",
                "event_type": "claim.filed",
                "timestamp": "2026-03-02T00:08:07Z",
                "agent_id": "a-claimant",
                "task_id": task_id,
                "summary": "Claim filed for Disputed Task",
                "payload": json.dumps({"claim_id": "clm-001", "title": "Disputed Task", "claimant_name": "Claimant"}),
            },
        })
        assert resp.status_code in (200, 201)

        row = read_one(initialized_db, "SELECT * FROM court_claims WHERE claim_id = ?", ("clm-001",))
        assert row is not None
        assert row["task_id"] == task_id
        assert row["claimant_id"] == "a-claimant"
        assert row["respondent_id"] == "a-respondent"
        assert row["reason"] == "Work does not meet specification"
        assert row["status"] == "filed"


class TestCourtRebuttals:

    async def test_submit_rebuttal_writes_row(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        task_id = await _setup_prerequisite_data(gw_client)

        # File claim first
        await gw_client.post("/court/claims", json={
            "claim_id": "clm-002",
            "task_id": task_id,
            "claimant_id": "a-claimant",
            "respondent_id": "a-respondent",
            "reason": "Incomplete work",
            "status": "filed",
            "rebuttal_deadline": "2026-03-02T06:30:00Z",
            "filed_at": "2026-03-02T00:08:07Z",
            "event": {
                "event_source": "court",
                "event_type": "claim.filed",
                "timestamp": "2026-03-02T00:08:07Z",
                "agent_id": "a-claimant",
                "task_id": task_id,
                "summary": "Claim filed",
                "payload": "{}",
            },
        })

        resp = await gw_client.post("/court/rebuttals", json={
            "rebuttal_id": "reb-001",
            "claim_id": "clm-002",
            "agent_id": "a-respondent",
            "content": "I followed the specification exactly",
            "submitted_at": "2026-03-02T00:16:15Z",
            "event": {
                "event_source": "court",
                "event_type": "rebuttal.submitted",
                "timestamp": "2026-03-02T00:16:15Z",
                "agent_id": "a-respondent",
                "task_id": task_id,
                "summary": "Rebuttal submitted",
                "payload": json.dumps({"claim_id": "clm-002", "respondent_name": "Respondent"}),
            },
        })
        assert resp.status_code in (200, 201)

        row = read_one(initialized_db, "SELECT * FROM court_rebuttals WHERE rebuttal_id = ?", ("reb-001",))
        assert row is not None
        assert row["claim_id"] == "clm-002"
        assert row["agent_id"] == "a-respondent"
        assert row["content"] == "I followed the specification exactly"


class TestCourtRulings:

    async def test_record_ruling_writes_row(
        self, gw_client: httpx.AsyncClient, initialized_db: str
    ):
        task_id = await _setup_prerequisite_data(gw_client)

        # File claim
        await gw_client.post("/court/claims", json={
            "claim_id": "clm-003",
            "task_id": task_id,
            "claimant_id": "a-claimant",
            "respondent_id": "a-respondent",
            "reason": "Missing features",
            "status": "filed",
            "rebuttal_deadline": "2026-03-02T06:30:00Z",
            "filed_at": "2026-03-02T00:08:07Z",
            "event": {
                "event_source": "court",
                "event_type": "claim.filed",
                "timestamp": "2026-03-02T00:08:07Z",
                "agent_id": "a-claimant",
                "task_id": task_id,
                "summary": "Claim filed",
                "payload": "{}",
            },
        })

        resp = await gw_client.post("/court/rulings", json={
            "ruling_id": "rul-001",
            "claim_id": "clm-003",
            "task_id": task_id,
            "worker_pct": 70,
            "summary": "Worker completed most requirements but missed error handling",
            "judge_votes": json.dumps([
                {"judge": "judge-1", "worker_pct": 70, "reasoning": "Mostly complete"},
                {"judge": "judge-2", "worker_pct": 65, "reasoning": "Good effort"},
                {"judge": "judge-3", "worker_pct": 75, "reasoning": "Minor gaps"},
            ]),
            "ruled_at": "2026-03-02T00:32:30Z",
            "event": {
                "event_source": "court",
                "event_type": "ruling.delivered",
                "timestamp": "2026-03-02T00:32:30Z",
                "agent_id": None,
                "task_id": task_id,
                "summary": "Ruling: 70% to worker",
                "payload": json.dumps({"ruling_id": "rul-001", "worker_pct": 70}),
            },
        })
        assert resp.status_code in (200, 201)

        row = read_one(initialized_db, "SELECT * FROM court_rulings WHERE ruling_id = ?", ("rul-001",))
        assert row is not None
        assert row["claim_id"] == "clm-003"
        assert row["task_id"] == task_id
        assert row["worker_pct"] == 70
        assert "most requirements" in row["summary"]
        assert row["judge_votes"] is not None
        votes = json.loads(row["judge_votes"])
        assert len(votes) == 3
```

## Tier 1 Verification

**IMPORTANT:** After implementing all the test files above, you MUST run verification. These tests talk directly to the DB Gateway's in-process ASGI app, so you need to run them from the `services/db-gateway` directory where the DB Gateway venv lives.

**However, since these are cross-service tests at the project root, you need to set up a test environment first.**

### Option A: Run from db-gateway service (simplest)

Copy the test files into the db-gateway service's test tree temporarily:

Actually, the better approach is to create a `pyproject.toml` at the project root `tests/` level. But that adds complexity.

### Recommended approach: Run with db-gateway's venv

```bash
cd /Users/flo/Developer/github/agent-economy/services/db-gateway
uv run pytest ../../tests/integration/ -v --tb=short -x 2>&1 | head -100
```

If this doesn't work due to import paths, try:

```bash
cd /Users/flo/Developer/github/agent-economy
PYTHONPATH=services/db-gateway/src:tests uv run --directory services/db-gateway pytest tests/integration/ -v --tb=short -x 2>&1 | head -100
```

**Expected outcome:** All tests pass (they test the DB Gateway directly, not through the individual services).

**Troubleshooting:**
- If `import db_gateway_service` fails: You need to run from the db-gateway service directory or add it to PYTHONPATH
- If `import tests.integration.conftest` fails: Add `PYTHONPATH=tests` or use relative imports
- If schema.sql path is wrong: Check that the path resolution works from the test's location
- If `clear_settings_cache` doesn't exist: Check the DB Gateway's config.py for the correct function name

**Adjust the tests as needed** to make them pass. The patterns shown are templates — the exact payloads may need tweaking based on what the DB Gateway routers actually accept. Read the router code in `services/db-gateway/src/db_gateway_service/routers/` to verify the exact expected payloads.

---

# TIER 2: Verify Integration Tests Fail When DB Gateway is Offline

**Ticket:** `agent-economy-nqb6`

## Goal

Write tests that verify services return clear 503 or error responses when the DB Gateway is unreachable. This validates that services truly depend on the gateway.

## Test Architecture

Each test:
1. Starts a service configured to use a DB Gateway at a URL that doesn't exist (e.g., `http://localhost:19999`)
2. Makes API calls
3. Verifies the service returns a clear error (not a hang, not a silent failure)

### File: `tests/integration/test_services_without_gateway.py`

```python
"""Integration tests: Services fail clearly when DB Gateway is offline.

Verifies each service returns 502/503 or clear error messages when
the DB Gateway is unreachable, not silent failures or hangs.
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = [pytest.mark.integration]

# A port that nothing is listening on
DEAD_GATEWAY_URL = "http://localhost:19999"


class TestIdentityWithoutGateway:
    """Identity service returns errors when DB Gateway is offline."""

    async def test_register_agent_fails_with_clear_error(self):
        """POST /agents/register should fail, not hang."""
        # We test at the GatewayAgentStore level since we can't easily
        # spin up the full Identity service without its own setup
        from identity_service.services.gateway_agent_store import GatewayAgentStore

        store = GatewayAgentStore(base_url=DEAD_GATEWAY_URL, timeout_seconds=2)
        try:
            with pytest.raises((httpx.ConnectError, httpx.TimeoutException, RuntimeError)):
                await store.insert(name="Test", public_key="ed25519:AAAA=")
        finally:
            await store.close()

    async def test_get_agent_fails_with_clear_error(self):
        from identity_service.services.gateway_agent_store import GatewayAgentStore

        store = GatewayAgentStore(base_url=DEAD_GATEWAY_URL, timeout_seconds=2)
        try:
            with pytest.raises((httpx.ConnectError, httpx.TimeoutException, RuntimeError)):
                await store.get_by_id("a-nonexistent")
        finally:
            await store.close()

    async def test_list_agents_fails_with_clear_error(self):
        from identity_service.services.gateway_agent_store import GatewayAgentStore

        store = GatewayAgentStore(base_url=DEAD_GATEWAY_URL, timeout_seconds=2)
        try:
            with pytest.raises((httpx.ConnectError, httpx.TimeoutException, RuntimeError)):
                await store.list_all()
        finally:
            await store.close()


class TestCentralBankWithoutGateway:
    """Central Bank returns errors when DB Gateway is offline."""

    async def test_create_account_fails_with_clear_error(self):
        from central_bank_service.services.gateway_ledger_store import GatewayLedgerStore

        store = GatewayLedgerStore(base_url=DEAD_GATEWAY_URL, timeout_seconds=2)
        try:
            with pytest.raises((httpx.ConnectError, httpx.TimeoutException, RuntimeError)):
                store.create_account("a-test", 0)
        finally:
            store.close()

    async def test_credit_fails_with_clear_error(self):
        from central_bank_service.services.gateway_ledger_store import GatewayLedgerStore

        store = GatewayLedgerStore(base_url=DEAD_GATEWAY_URL, timeout_seconds=2)
        try:
            with pytest.raises((httpx.ConnectError, httpx.TimeoutException, RuntimeError)):
                store.credit("a-test", 100, "ref-1")
        finally:
            store.close()

    async def test_escrow_lock_fails_with_clear_error(self):
        from central_bank_service.services.gateway_ledger_store import GatewayLedgerStore

        store = GatewayLedgerStore(base_url=DEAD_GATEWAY_URL, timeout_seconds=2)
        try:
            with pytest.raises((httpx.ConnectError, httpx.TimeoutException, RuntimeError)):
                store.escrow_lock("a-test", 100, "t-test")
        finally:
            store.close()
```

**NOTE:** The offline tests import Gateway stores from individual services. You need to run them with the appropriate service venvs on the PYTHONPATH:

```bash
cd /Users/flo/Developer/github/agent-economy
PYTHONPATH=services/identity/src:services/central-bank/src:services/task-board/src:services/reputation/src:services/court/src:services/db-gateway/src:tests \
  uv run --directory services/db-gateway pytest tests/integration/test_services_without_gateway.py -v --tb=short -x --timeout=10 2>&1 | head -60
```

**Expected outcome:** All tests pass — each one verifies that the store raises an error (ConnectError/TimeoutException/RuntimeError) instead of hanging.

---

# TIER 3: Puppet Agent Demo System

**Ticket:** `agent-economy-7jt5`

## Goal

Refactor the existing `tools/src/demo_replay/` system into a proper puppet agent architecture. The existing code already works — it uses `DemoAgent` objects with Ed25519 keys, has `clients.py` with HTTP wrappers for all services, and reads YAML scenarios. The main improvements:

1. **Better variable/reference system** — already partially implemented with `_refs` dict
2. **Cleaner separation** — extract a `PuppetAgent` class from `DemoAgent`
3. **Error handling improvements** — fail-fast with clear messages
4. **Scenario validation** — validate YAML before execution

## Design

The existing code is already 80% there. The key refactoring:

1. **Rename `DemoAgent` → `PuppetAgent`** in `wallet.py` (or create alias)
2. **Add a `PuppetMaster` class** in a new `master.py` that wraps `ReplayEngine`
3. **Improve error handling** — each step gets try/except with clear failure messages
4. **Add scenario validation** — check all refs exist before running
5. **Support feedback steps** — add `feedback` and `reveal_feedback` actions
6. **Keep backward compatibility** — existing scenarios still work

### File: `tools/src/demo_replay/puppet.py` (NEW)

```python
"""Puppet agent — thin wrapper for Ed25519 agent identity.

Re-exports DemoAgent as PuppetAgent for clarity. The underlying
implementation is the same: in-memory keypair, JWS signing, and
auth header generation.
"""
from __future__ import annotations

from demo_replay.wallet import DemoAgent

# PuppetAgent is just DemoAgent with a clearer name
PuppetAgent = DemoAgent
```

### File: `tools/src/demo_replay/master.py` (NEW)

```python
"""Puppet Master — orchestrates scenario execution.

Reads a YAML choreography file, instantiates puppet agents, and executes
steps sequentially. Wraps ReplayEngine with better error handling and
validation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Console

from demo_replay.engine import ReplayEngine, load_scenario

console = Console()


class ScenarioValidationError(Exception):
    """Raised when a scenario file has structural problems."""


def validate_scenario(scenario: dict[str, Any]) -> list[str]:
    """Validate scenario structure and return list of warnings.

    Raises ScenarioValidationError for fatal issues.
    """
    warnings: list[str] = []
    agents = {a["handle"] for a in scenario.get("agents", [])}

    for i, step in enumerate(scenario.get("steps", []), 1):
        action = step.get("action")
        if not action:
            msg = f"Step {i}: missing 'action' field"
            raise ScenarioValidationError(msg)

        # Check agent references exist
        for field in ("agent", "poster", "bidder", "worker"):
            if field in step and step[field] not in agents:
                msg = f"Step {i}: unknown agent '{step[field]}' (not in agents list)"
                raise ScenarioValidationError(msg)

    return warnings


class PuppetMaster:
    """High-level orchestrator for scenario execution."""

    def __init__(self, scenario_path: Path) -> None:
        self.scenario_path = scenario_path
        self.scenario = load_scenario(scenario_path)
        self._engine: ReplayEngine | None = None

    def validate(self) -> list[str]:
        """Validate the scenario file. Returns warnings."""
        return validate_scenario(self.scenario)

    async def run(self) -> None:
        """Execute the scenario."""
        warnings = self.validate()
        for w in warnings:
            console.print(f"  [yellow]Warning: {w}[/yellow]")

        self._engine = ReplayEngine(self.scenario)
        await self._engine.run()
```

### File: `tools/src/demo_replay/clients.py` — ADD feedback methods

Add these two functions to the END of the existing `clients.py` file (do NOT modify existing functions):

```python
async def submit_feedback(
    client: httpx.AsyncClient,
    agent: DemoAgent,
    task_id: str,
    to_agent_id: str,
    role: str,
    category: str,
    rating: str,
    comment: str,
    reputation_url: str = "http://localhost:8004",
) -> dict[str, Any]:
    """Submit sealed feedback for a task."""
    url = f"{reputation_url}/feedback"
    token = agent.sign_jws(
        {
            "action": "submit_feedback",
            "task_id": task_id,
            "to_agent_id": to_agent_id,
            "role": role,
            "category": category,
            "rating": rating,
            "comment": comment,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]


async def reveal_feedback(
    client: httpx.AsyncClient,
    agent: DemoAgent,
    task_id: str,
    reputation_url: str = "http://localhost:8004",
) -> dict[str, Any]:
    """Reveal feedback for a task (both parties have submitted)."""
    url = f"{reputation_url}/tasks/{task_id}/feedback/reveal"
    token = agent.sign_jws(
        {
            "action": "reveal_feedback",
            "task_id": task_id,
        }
    )
    resp = await client.post(url, json={"token": token})
    resp.raise_for_status()
    return resp.json()  # type: ignore[no-any-return]
```

### File: `tools/src/demo_replay/engine.py` — ADD feedback handlers

Add these methods to the `ReplayEngine` class (do NOT modify existing methods, only ADD new ones):

Add a new `case "feedback":` and `case "reveal_feedback":` to the `_execute_step` match statement, and add the handler methods:

**In `_execute_step`, add to the match statement:**
```python
            case "feedback":
                await self._do_feedback(http, step)
            case "reveal_feedback":
                await self._do_reveal_feedback(http, step)
```

**New methods to add at the end of the class:**
```python
    async def _do_feedback(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        agent_handle = step["agent"]
        agent = self.agents[agent_handle]
        task_id = self._resolve_task_id(step, agent_handle)
        result = await clients.submit_feedback(
            http,
            agent,
            task_id=task_id,
            to_agent_id=step["to_agent_id"],
            role=step["role"],
            category=step["category"],
            rating=step["rating"],
            comment=step.get("comment", ""),
        )
        console.print(
            f"  [green]{agent.name} submitted {step['category']} feedback[/green]"
            f" ({step['rating']})"
        )

    async def _do_reveal_feedback(
        self, http: httpx.AsyncClient, step: dict[str, Any]
    ) -> None:
        agent_handle = step["agent"]
        agent = self.agents[agent_handle]
        task_id = self._resolve_task_id(step, agent_handle)
        await clients.reveal_feedback(http, agent, task_id)
        console.print(
            f"  [green]{agent.name} revealed feedback[/green] for task"
        )
```

### Scenario Enhancement: `tools/scenarios/quick.yaml`

Do NOT modify `quick.yaml`. Instead, create a NEW scenario file:

### File: `tools/scenarios/full-lifecycle.yaml` (NEW)

```yaml
# Full lifecycle demo: 3 agents, happy path + dispute + feedback (~40s)

name: "Full Lifecycle — Task Economy Demo"
description: >
  Demonstrates the complete task lifecycle including feedback exchange.
  Act 1: Register agents and fund accounts.
  Act 2: Happy path — post task, bid, accept, submit, approve, feedback.
  Act 3: Dispute path — post task, bid, accept, submit, dispute.
default_delay: 2.0

agents:
  - handle: alice
    name: "Alice (Poster)"
  - handle: bob
    name: "Bob (Worker)"
  - handle: carol
    name: "Carol (Worker)"

steps:
  # ── Act 1: Setup ──────────────────────────────────────────
  - action: register
    agent: alice
    delay: 1.0

  - action: register
    agent: bob
    delay: 1.0

  - action: register
    agent: carol
    delay: 1.0

  - action: fund
    agent: alice
    amount: 5000
    delay: 1.5

  - action: fund
    agent: bob
    amount: 1000
    delay: 1.0

  - action: fund
    agent: carol
    amount: 1000
    delay: 1.5

  # ── Act 2: Happy path with feedback ────────────────────────
  - action: post_task
    poster: alice
    ref: login_task
    title: "Implement login page"
    spec: "Build a responsive login form with email and password fields."
    reward: 200
    delay: 3.0

  - action: bid
    bidder: bob
    task_ref: login_task
    amount: 180
    delay: 2.0

  - action: accept_bid
    poster: alice
    bidder: bob
    task_ref: login_task
    delay: 2.5

  - action: upload_asset
    worker: bob
    task_ref: login_task
    filename: "login-page.html"
    content: |
      <!DOCTYPE html><html><body><form>Login form</form></body></html>
    delay: 2.0

  - action: submit_deliverable
    worker: bob
    task_ref: login_task
    delay: 3.0

  - action: approve
    poster: alice
    task_ref: login_task
    delay: 3.0

  # ── Act 3: Dispute path ──────────────────────────────────
  - action: post_task
    poster: alice
    ref: api_task
    title: "Design REST API specification"
    spec: "Design a complete RESTful API spec for a todo application."
    reward: 150
    delay: 3.0

  - action: bid
    bidder: carol
    task_ref: api_task
    amount: 140
    delay: 2.5

  - action: accept_bid
    poster: alice
    bidder: carol
    task_ref: api_task
    delay: 2.5

  - action: upload_asset
    worker: carol
    task_ref: api_task
    filename: "api-spec.md"
    content: |
      # Todo API
      - GET /todos - List todos
      - POST /todos - Create todo
    delay: 2.0

  - action: submit_deliverable
    worker: carol
    task_ref: api_task
    delay: 3.0

  - action: dispute
    poster: alice
    task_ref: api_task
    reason: "Missing PUT/PATCH endpoints and error response schemas."
```

## Tier 3 Verification

```bash
# Verify the new files parse correctly
cd /Users/flo/Developer/github/agent-economy/tools
uv run python -c "from demo_replay.puppet import PuppetAgent; print('PuppetAgent OK')"
uv run python -c "from demo_replay.master import PuppetMaster; print('PuppetMaster OK')"
uv run python -c "import yaml; s = yaml.safe_load(open('../tools/scenarios/full-lifecycle.yaml')); print(f'Scenario: {s[\"name\"]} with {len(s[\"steps\"])} steps')"

# Verify existing quick.yaml scenario still loads
uv run python -c "from demo_replay.engine import load_scenario; from pathlib import Path; s = load_scenario(Path('../tools/scenarios/quick.yaml')); print(f'Quick: {len(s[\"steps\"])} steps OK')"
```

### E2E Verification (requires all services running)

```bash
cd /Users/flo/Developer/github/agent-economy

# Start all services
just stop-all 2>/dev/null; just start-all

# Wait for services to be healthy
sleep 10
just status

# Run the quick demo to verify nothing is broken
just demo

# Run the full-lifecycle demo
cd tools && uv run python -m demo_replay ../tools/scenarios/full-lifecycle.yaml
```

---

## Final Verification — All Tiers

After all three tiers are complete, run the full project CI:

```bash
cd /Users/flo/Developer/github/agent-economy

# Run per-service CI for DB Gateway (where most test infra lives)
cd services/db-gateway && just ci-quiet && cd ../..

# Run the integration tests
PYTHONPATH=services/db-gateway/src:tests \
  uv run --directory services/db-gateway pytest tests/integration/ -v --tb=short 2>&1 | tail -30

# Run full project CI
just ci-all-quiet 2>&1 | tail -30

# If services are running, run E2E tests
just test-e2e 2>&1 | tail -30
```

---

## Troubleshooting Guide

### Common Issues

1. **Import errors for `db_gateway_service`**: Run tests from `services/db-gateway/` or add `services/db-gateway/src` to PYTHONPATH
2. **Schema path not found**: The `SCHEMA_PATH` in conftest.py assumes tests/ is at the project root. Verify the relative path.
3. **`clear_settings_cache` not found**: Check `services/db-gateway/src/db_gateway_service/config.py` for the exact exported function name. It may be part of a tuple returned by `create_settings_loader`.
4. **ASGITransport errors**: Make sure `httpx` version supports `ASGITransport`. The DB Gateway's venv should have it.
5. **Foreign key violations**: Tests must create prerequisite data in the correct order: agents first, then accounts, then escrow, then tasks, then bids/claims.
6. **Busy database**: Tests use temp files per test — should not conflict. If issues arise, add `PRAGMA busy_timeout = 5000` in the test setup.
7. **Existing test failures**: Do NOT modify existing test files. If existing tests break, it means a code change affected them — investigate the root cause.

### Priority Order

If time is limited, implement in this order:
1. **Tier 1** (the blocking ticket) — integration tests that verify DB writes
2. **Tier 3** (puppet agents) — the most user-visible feature
3. **Tier 2** (offline tests) — important but lower impact

Tier 2 is independent of Tier 3, but Tier 3 depends on Tier 1 being complete.

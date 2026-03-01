# Phase 8 — Tests

## Working Directory

All paths relative to `services/db-gateway/`.

---

## File 1: `tests/conftest.py`

Create this file:

```python
"""Root conftest — shared fixtures for all test types."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def schema_sql() -> str:
    """Load the shared economy.db schema."""
    schema_path = Path(__file__).parent.parent.parent.parent / "docs" / "specifications" / "schema.sql"
    return schema_path.read_text()


@pytest.fixture
def tmp_db_path() -> Iterator[str]:
    """Create a temporary database file path."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    try:
        os.unlink(path)
    except OSError:
        pass


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
    agent_id: str | None = "a-test",
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
```

---

## File 2: `tests/unit/conftest.py`

Create this file:

```python
"""Unit test fixtures."""

from __future__ import annotations

import os
import tempfile
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from db_gateway_service.config import clear_settings_cache
from db_gateway_service.core.state import init_app_state, reset_app_state
from db_gateway_service.services.db_writer import DbWriter

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_state() -> Iterator[None]:
    """Reset app state and settings cache between tests."""
    yield
    reset_app_state()
    clear_settings_cache()


@pytest.fixture
def db_writer(initialized_db: str) -> Iterator[DbWriter]:
    """Create a DbWriter with an initialized test database."""
    writer = DbWriter(
        db_path=initialized_db,
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=None,
    )
    yield writer
    writer.close()


@pytest.fixture
def app_with_writer(db_writer: DbWriter) -> Iterator[TestClient]:
    """Create a FastAPI test client with a real DbWriter."""
    state = init_app_state()
    state.db_writer = db_writer

    from db_gateway_service.app import create_app  # noqa: PLC0415

    # Override settings to avoid needing config.yaml
    os.environ["CONFIG_PATH"] = _create_test_config(db_writer._db_path)
    clear_settings_cache()

    app = create_app()
    with TestClient(app) as client:
        # Re-inject the db_writer since lifespan creates a new one
        state = init_app_state()
        state.db_writer = db_writer
        yield client

    if "CONFIG_PATH" in os.environ:
        config_path = os.environ.pop("CONFIG_PATH")
        try:
            os.unlink(config_path)
        except OSError:
            pass


def _create_test_config(db_path: str) -> str:
    """Write a temporary config.yaml for testing."""
    config_content = f"""
service:
  name: "db-gateway-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18006
  log_level: "warning"

logging:
  level: "WARNING"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "../../docs/specifications/schema.sql"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(config_content)
        return f.name
```

---

## File 3: `tests/unit/test_config.py`

Create this file:

```python
"""Unit tests for configuration loading."""

from __future__ import annotations

import pytest

from db_gateway_service.config import Settings


@pytest.mark.unit
class TestConfig:
    """Test configuration validation."""

    def test_valid_config_loads(self) -> None:
        """Settings can be constructed with valid data."""
        settings = Settings(
            service={"name": "db-gateway", "version": "0.1.0"},
            server={"host": "0.0.0.0", "port": 8006, "log_level": "info"},
            logging={"level": "INFO", "format": "json"},
            database={
                "path": "data/economy.db",
                "schema_path": "../../docs/specifications/schema.sql",
                "busy_timeout_ms": 5000,
                "journal_mode": "wal",
            },
            request={"max_body_size": 1048576},
        )
        assert settings.service.name == "db-gateway"
        assert settings.server.port == 8006
        assert settings.database.busy_timeout_ms == 5000

    def test_extra_fields_rejected(self) -> None:
        """Extra fields cause validation errors (ConfigDict extra='forbid')."""
        with pytest.raises(Exception):
            Settings(
                service={"name": "db-gateway", "version": "0.1.0", "extra": True},
                server={"host": "0.0.0.0", "port": 8006, "log_level": "info"},
                logging={"level": "INFO", "format": "json"},
                database={
                    "path": "data/economy.db",
                    "schema_path": "../../docs/specifications/schema.sql",
                    "busy_timeout_ms": 5000,
                    "journal_mode": "wal",
                },
                request={"max_body_size": 1048576},
            )

    def test_missing_required_field(self) -> None:
        """Missing required fields cause validation errors."""
        with pytest.raises(Exception):
            Settings(
                service={"name": "db-gateway", "version": "0.1.0"},
                server={"host": "0.0.0.0", "port": 8006, "log_level": "info"},
                logging={"level": "INFO", "format": "json"},
                # database section missing
                request={"max_body_size": 1048576},
            )
```

---

## File 4: `tests/unit/test_db_writer.py`

Create this file:

```python
"""Unit tests for the DbWriter service layer."""

from __future__ import annotations

from typing import Any

import pytest

from db_gateway_service.services.db_writer import DbWriter

from tests.conftest import make_event


@pytest.mark.unit
class TestDbWriterIdentity:
    """Tests for identity operations."""

    def test_register_agent(self, db_writer: DbWriter) -> None:
        """Successfully register a new agent."""
        result = db_writer.register_agent({
            "agent_id": "a-test-1",
            "name": "Alice",
            "public_key": "ed25519:AAAA",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        })
        assert result["agent_id"] == "a-test-1"
        assert result["event_id"] > 0

    def test_register_agent_duplicate_public_key(self, db_writer: DbWriter) -> None:
        """Duplicate public key raises PUBLIC_KEY_EXISTS."""
        data = {
            "agent_id": "a-test-1",
            "name": "Alice",
            "public_key": "ed25519:AAAA",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        }
        db_writer.register_agent(data)

        from service_commons.exceptions import ServiceError

        with pytest.raises(ServiceError, match="PUBLIC_KEY_EXISTS"):
            db_writer.register_agent({
                "agent_id": "a-test-2",
                "name": "Bob",
                "public_key": "ed25519:AAAA",
                "registered_at": "2026-02-28T11:00:00Z",
                "event": make_event(),
            })


@pytest.mark.unit
class TestDbWriterBank:
    """Tests for bank operations."""

    def _register_agent(self, db_writer: DbWriter, agent_id: str = "a-test-1") -> None:
        """Helper: register an agent (prerequisite for account creation)."""
        db_writer.register_agent({
            "agent_id": agent_id,
            "name": "Test",
            "public_key": f"ed25519:{agent_id}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        })

    def test_create_account_zero_balance(self, db_writer: DbWriter) -> None:
        """Create an account with zero balance."""
        self._register_agent(db_writer)
        result = db_writer.create_account({
            "account_id": "a-test-1",
            "balance": 0,
            "created_at": "2026-02-28T10:00:00Z",
            "event": make_event(source="bank", event_type="account.created"),
        })
        assert result["account_id"] == "a-test-1"

    def test_credit_account(self, db_writer: DbWriter) -> None:
        """Credit an existing account."""
        self._register_agent(db_writer)
        db_writer.create_account({
            "account_id": "a-test-1",
            "balance": 0,
            "created_at": "2026-02-28T10:00:00Z",
            "event": make_event(source="bank", event_type="account.created"),
        })
        result = db_writer.credit_account({
            "tx_id": "tx-1",
            "account_id": "a-test-1",
            "amount": 100,
            "reference": "salary_1",
            "timestamp": "2026-02-28T10:05:00Z",
            "event": make_event(source="bank", event_type="salary.paid"),
        })
        assert result["balance_after"] == 100

    def test_credit_account_not_found(self, db_writer: DbWriter) -> None:
        """Credit a nonexistent account raises ACCOUNT_NOT_FOUND."""
        from service_commons.exceptions import ServiceError

        with pytest.raises(ServiceError, match="ACCOUNT_NOT_FOUND"):
            db_writer.credit_account({
                "tx_id": "tx-1",
                "account_id": "a-nonexistent",
                "amount": 100,
                "reference": "salary_1",
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            })


@pytest.mark.unit
class TestDbWriterEscrow:
    """Tests for escrow operations."""

    def _setup_funded_account(
        self, db_writer: DbWriter, agent_id: str = "a-poster", balance: int = 500
    ) -> None:
        """Helper: register agent + create account + credit."""
        db_writer.register_agent({
            "agent_id": agent_id,
            "name": "Poster",
            "public_key": f"ed25519:{agent_id}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        })
        db_writer.create_account({
            "account_id": agent_id,
            "balance": 0,
            "created_at": "2026-02-28T10:00:00Z",
            "event": make_event(source="bank", event_type="account.created"),
        })
        if balance > 0:
            db_writer.credit_account({
                "tx_id": f"tx-fund-{agent_id}",
                "account_id": agent_id,
                "amount": balance,
                "reference": "initial_fund",
                "timestamp": "2026-02-28T10:01:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            })

    def test_escrow_lock(self, db_writer: DbWriter) -> None:
        """Lock funds in escrow."""
        self._setup_funded_account(db_writer, "a-poster", 500)
        result = db_writer.escrow_lock({
            "escrow_id": "esc-1",
            "payer_account_id": "a-poster",
            "amount": 100,
            "task_id": "t-1",
            "created_at": "2026-02-28T10:10:00Z",
            "tx_id": "tx-lock-1",
            "event": make_event(source="bank", event_type="escrow.locked", task_id="t-1"),
        })
        assert result["escrow_id"] == "esc-1"
        assert result["balance_after"] == 400

    def test_escrow_lock_insufficient_funds(self, db_writer: DbWriter) -> None:
        """Escrow lock with insufficient balance raises INSUFFICIENT_FUNDS."""
        self._setup_funded_account(db_writer, "a-poster", 50)
        from service_commons.exceptions import ServiceError

        with pytest.raises(ServiceError, match="INSUFFICIENT_FUNDS"):
            db_writer.escrow_lock({
                "escrow_id": "esc-1",
                "payer_account_id": "a-poster",
                "amount": 100,
                "task_id": "t-1",
                "created_at": "2026-02-28T10:10:00Z",
                "tx_id": "tx-lock-1",
                "event": make_event(source="bank", event_type="escrow.locked", task_id="t-1"),
            })
```

---

## File 5: `tests/unit/routers/__init__.py`

Empty file (already created in Phase 1).

---

## File 6: `tests/unit/routers/conftest.py`

Create this file:

```python
"""Router-level test fixtures."""
```

---

## File 7: `tests/unit/routers/test_health.py`

Create this file:

```python
"""Unit tests for the health endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from db_gateway_service.core.state import get_app_state, init_app_state


@pytest.mark.unit
class TestHealth:
    """Health endpoint tests."""

    def test_health_returns_ok(self, app_with_writer: TestClient) -> None:
        """GET /health returns status ok."""
        response = app_with_writer.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "started_at" in data
        assert "database_size_bytes" in data
        assert "total_events" in data
```

---

## File 8: `tests/unit/routers/test_identity.py`

Create this file:

```python
"""Unit tests for identity router endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_event


@pytest.mark.unit
class TestRegisterAgent:
    """POST /identity/agents tests."""

    def test_register_agent_success(self, app_with_writer: TestClient) -> None:
        """Successfully register a new agent."""
        response = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": "a-test-1",
                "name": "Alice",
                "public_key": "ed25519:AAAA",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["agent_id"] == "a-test-1"
        assert "event_id" in data

    def test_register_agent_missing_field(self, app_with_writer: TestClient) -> None:
        """Missing required field returns 400."""
        response = app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": "a-test-1",
                "event": make_event(),
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "MISSING_FIELD"
```

---

## File 9: `tests/unit/routers/test_bank.py`

Create this file:

```python
"""Unit tests for bank router endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_event


def _register_and_fund(client: TestClient, agent_id: str = "a-poster", balance: int = 500) -> None:
    """Helper: register agent + create account + credit."""
    client.post(
        "/identity/agents",
        json={
            "agent_id": agent_id,
            "name": "Test",
            "public_key": f"ed25519:{agent_id}",
            "registered_at": "2026-02-28T10:00:00Z",
            "event": make_event(),
        },
    )
    client.post(
        "/bank/accounts",
        json={
            "account_id": agent_id,
            "balance": 0,
            "created_at": "2026-02-28T10:00:00Z",
            "event": make_event(source="bank", event_type="account.created"),
        },
    )
    if balance > 0:
        client.post(
            "/bank/credit",
            json={
                "tx_id": f"tx-fund-{agent_id}",
                "account_id": agent_id,
                "amount": balance,
                "reference": "initial_fund",
                "timestamp": "2026-02-28T10:01:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )


@pytest.mark.unit
class TestBankAccounts:
    """POST /bank/accounts tests."""

    def test_create_account(self, app_with_writer: TestClient) -> None:
        """Create an account with zero balance."""
        app_with_writer.post(
            "/identity/agents",
            json={
                "agent_id": "a-test-1",
                "name": "Alice",
                "public_key": "ed25519:AAAA",
                "registered_at": "2026-02-28T10:00:00Z",
                "event": make_event(),
            },
        )
        response = app_with_writer.post(
            "/bank/accounts",
            json={
                "account_id": "a-test-1",
                "balance": 0,
                "created_at": "2026-02-28T10:00:00Z",
                "event": make_event(source="bank", event_type="account.created"),
            },
        )
        assert response.status_code == 201


@pytest.mark.unit
class TestBankCredit:
    """POST /bank/credit tests."""

    def test_credit_account(self, app_with_writer: TestClient) -> None:
        """Credit an existing account."""
        _register_and_fund(app_with_writer, "a-test-1", balance=0)
        response = app_with_writer.post(
            "/bank/credit",
            json={
                "tx_id": "tx-credit-1",
                "account_id": "a-test-1",
                "amount": 50,
                "reference": "salary_1",
                "timestamp": "2026-02-28T10:05:00Z",
                "event": make_event(source="bank", event_type="salary.paid"),
            },
        )
        assert response.status_code == 200
        assert response.json()["balance_after"] == 50


@pytest.mark.unit
class TestEscrowLock:
    """POST /bank/escrow/lock tests."""

    def test_escrow_lock(self, app_with_writer: TestClient) -> None:
        """Lock funds in escrow."""
        _register_and_fund(app_with_writer, "a-poster", 500)
        response = app_with_writer.post(
            "/bank/escrow/lock",
            json={
                "escrow_id": "esc-1",
                "payer_account_id": "a-poster",
                "amount": 100,
                "task_id": "t-1",
                "created_at": "2026-02-28T10:10:00Z",
                "tx_id": "tx-lock-1",
                "event": make_event(source="bank", event_type="escrow.locked", task_id="t-1"),
            },
        )
        assert response.status_code == 201
        assert response.json()["balance_after"] == 400
```

---

## File 10: `tests/unit/routers/test_board.py`

Create this file:

```python
"""Unit tests for board router endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_event


@pytest.mark.unit
class TestBoardTasks:
    """POST /board/tasks tests — placeholder."""

    def test_missing_field_returns_400(self, app_with_writer: TestClient) -> None:
        """Missing required field returns 400."""
        response = app_with_writer.post(
            "/board/tasks",
            json={"event": make_event(source="board", event_type="task.created")},
        )
        assert response.status_code == 400


@pytest.mark.unit
class TestBoardBids:
    """POST /board/bids tests — placeholder."""

    def test_missing_field_returns_400(self, app_with_writer: TestClient) -> None:
        """Missing required field returns 400."""
        response = app_with_writer.post(
            "/board/bids",
            json={"event": make_event(source="board", event_type="bid.submitted")},
        )
        assert response.status_code == 400


@pytest.mark.unit
class TestTaskStatusUpdate:
    """POST /board/tasks/{task_id}/status tests — placeholder."""

    def test_empty_updates_returns_400(self, app_with_writer: TestClient) -> None:
        """Empty updates object returns 400."""
        response = app_with_writer.post(
            "/board/tasks/t-1/status",
            json={
                "updates": {},
                "event": make_event(source="board", event_type="task.accepted"),
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "EMPTY_UPDATES"
```

---

## File 11: `tests/unit/routers/test_reputation.py`

Create this file:

```python
"""Unit tests for reputation router endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_event


@pytest.mark.unit
class TestFeedback:
    """POST /reputation/feedback tests — placeholder."""

    def test_missing_field_returns_400(self, app_with_writer: TestClient) -> None:
        """Missing required field returns 400."""
        response = app_with_writer.post(
            "/reputation/feedback",
            json={"event": make_event(source="reputation", event_type="feedback.revealed")},
        )
        assert response.status_code == 400
```

---

## File 12: `tests/unit/routers/test_court.py`

Create this file:

```python
"""Unit tests for court router endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_event


@pytest.mark.unit
class TestClaims:
    """POST /court/claims tests — placeholder."""

    def test_missing_field_returns_400(self, app_with_writer: TestClient) -> None:
        """Missing required field returns 400."""
        response = app_with_writer.post(
            "/court/claims",
            json={"event": make_event(source="court", event_type="claim.filed")},
        )
        assert response.status_code == 400


@pytest.mark.unit
class TestRebuttals:
    """POST /court/rebuttals tests — placeholder."""

    def test_missing_field_returns_400(self, app_with_writer: TestClient) -> None:
        """Missing required field returns 400."""
        response = app_with_writer.post(
            "/court/rebuttals",
            json={"event": make_event(source="court", event_type="rebuttal.submitted")},
        )
        assert response.status_code == 400


@pytest.mark.unit
class TestRulings:
    """POST /court/rulings tests — placeholder."""

    def test_missing_field_returns_400(self, app_with_writer: TestClient) -> None:
        """Missing required field returns 400."""
        response = app_with_writer.post(
            "/court/rulings",
            json={"event": make_event(source="court", event_type="ruling.delivered")},
        )
        assert response.status_code == 400
```

---

## File 13: `tests/integration/conftest.py`

Create this file:

```python
"""Integration test fixtures."""

from __future__ import annotations

import pytest
import httpx


@pytest.fixture
def gateway_url() -> str:
    """Base URL for the running Database Gateway service."""
    return "http://localhost:8006"


@pytest.fixture
def gateway_client(gateway_url: str) -> httpx.Client:
    """HTTP client for the gateway."""
    return httpx.Client(base_url=gateway_url)
```

---

## File 14: `tests/integration/test_endpoints.py`

Create this file:

```python
"""Integration tests — require a running Database Gateway service."""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.integration
class TestHealthIntegration:
    """Integration tests for /health endpoint."""

    def test_health_check(self, gateway_client: httpx.Client) -> None:
        """GET /health returns 200 with expected fields."""
        response = gateway_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "database_size_bytes" in data
        assert "total_events" in data
```

---

## File 15: `tests/performance/conftest.py`

Create this file:

```python
"""Performance test fixtures."""
```

---

## File 16: `tests/performance/test_performance.py`

Create this file:

```python
"""Performance benchmarks for the Database Gateway."""

from __future__ import annotations

import pytest


@pytest.mark.performance
class TestWritePerformance:
    """Write throughput benchmarks — placeholder."""

    def test_placeholder(self) -> None:
        """Placeholder for performance tests."""
```

---

## Verification

```bash
cd services/db-gateway && uv run ruff check src/ tests/ && uv run ruff format --check src/ tests/
```

Must pass with zero errors. Then run unit tests:

```bash
cd services/db-gateway && just test-unit
```

Tests should pass. Some may fail if fixtures need adjustment — troubleshoot in Phase 9.

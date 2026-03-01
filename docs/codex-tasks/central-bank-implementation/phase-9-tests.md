# Phase 9 — Tests

## Working Directory

All paths relative to `services/central-bank/`.

---

## Task B9: Write test fixtures and config tests

### Step 9.1: Write tests/conftest.py

Create `services/central-bank/tests/conftest.py`:

```python
"""Shared test configuration."""
```

### Step 9.2: Write tests/unit/conftest.py

Create `services/central-bank/tests/unit/conftest.py`:

```python
"""Unit test fixtures."""

import pytest
from central_bank_service.config import clear_settings_cache
from central_bank_service.core.state import reset_app_state


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear settings cache and app state between tests."""
    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()
```

### Step 9.3: Write tests/unit/test_config.py

Create `services/central-bank/tests/unit/test_config.py`:

```python
"""Configuration loading tests."""

from __future__ import annotations

import os

import pytest
from central_bank_service.config import Settings, clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path):
    """Config loads correctly from a valid YAML file."""
    config_content = """
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8002
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/central-bank.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
platform:
  agent_id: "a-platform"
request:
  max_body_size: 1048576
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.service.name == "central-bank"
    assert settings.server.port == 8002
    assert settings.database.path == "data/central-bank.db"
    assert settings.identity.base_url == "http://localhost:8001"
    assert settings.platform.agent_id == "a-platform"
    assert settings.request.max_body_size == 1048576

    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path):
    """Config with extra fields causes validation error."""
    config_content = """
service:
  name: "central-bank"
  version: "0.1.0"
  unknown_field: true
server:
  host: "0.0.0.0"
  port: 8002
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/central-bank.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
platform:
  agent_id: "a-platform"
request:
  max_body_size: 1048576
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()

    with pytest.raises(Exception):
        get_settings()

    os.environ.pop("CONFIG_PATH", None)
```

### Step 9.4: Create test router directory and fixtures

Create empty `services/central-bank/tests/unit/routers/__init__.py`.

Create `services/central-bank/tests/unit/routers/conftest.py`:

```python
"""Router test fixtures with mocked Identity service."""

from __future__ import annotations

import base64
import json
import os
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from httpx import ASGITransport, AsyncClient
from joserfc import jws
from joserfc.jwk import OKPKey

from central_bank_service.app import create_app
from central_bank_service.config import clear_settings_cache
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.state import get_app_state, reset_app_state


PLATFORM_AGENT_ID = "a-platform-test-id"


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def make_jws_token(private_key: Ed25519PrivateKey, agent_id: str, payload: dict[str, Any]) -> str:
    """Create a JWS compact token signed by the given private key."""
    raw_private = private_key.private_bytes_raw()
    raw_public = private_key.public_key().public_bytes_raw()
    jwk_dict = {
        "kty": "OKP",
        "crv": "Ed25519",
        "d": base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode(),
        "x": base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode(),
    }
    key = OKPKey.import_key(jwk_dict)
    protected = {"alg": "EdDSA", "kid": agent_id}
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return jws.serialize_compact(protected, payload_bytes, key)


@pytest.fixture
def platform_keypair():
    """Generate a platform keypair."""
    return _generate_keypair()


@pytest.fixture
def agent_keypair():
    """Generate an agent keypair."""
    return _generate_keypair()


@pytest.fixture
async def app(tmp_path, platform_keypair):
    """Create a test app with a temporary database and mocked Identity client."""
    db_path = tmp_path / "test.db"
    config_content = f"""
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8002
  log_level: "info"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "{db_path}"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
request:
  max_body_size: 1048576
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        # Replace the real Identity client with a mock
        state = get_app_state()
        mock_identity = AsyncMock()
        mock_identity.close = AsyncMock()

        # Default: verify_jws succeeds by actually checking the token structure
        # Tests will configure specific behaviors
        state.identity_client = mock_identity

        yield test_app

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)


@pytest.fixture
async def client(app):
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
```

### Step 9.5: Run the config test

```bash
cd services/central-bank && uv run pytest tests/unit/test_config.py -v
```

Expected: PASS

### Step 9.6: Commit

```bash
git add services/central-bank/tests/
git commit -m "feat(central-bank): add test fixtures and config tests"
```

---

## Task B10: Write health endpoint tests

### Step 10.1: Write test_health.py

Create `services/central-bank/tests/unit/routers/test_health.py`:

```python
"""Health endpoint tests."""

from __future__ import annotations

import pytest


@pytest.mark.unit
async def test_health_returns_ok(client):
    """GET /health returns 200 with correct schema."""
    response = await client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["started_at"], str)
    assert data["total_accounts"] == 0
    assert data["total_escrowed"] == 0


@pytest.mark.unit
async def test_health_post_not_allowed(client):
    """POST /health returns 405."""
    response = await client.post("/health")
    assert response.status_code == 405
    assert response.json()["error"] == "METHOD_NOT_ALLOWED"
```

### Step 10.2: Run tests

```bash
cd services/central-bank && uv run pytest tests/unit/routers/test_health.py -v
```

Expected: PASS

### Step 10.3: Commit

```bash
git add services/central-bank/tests/unit/routers/test_health.py
git commit -m "test(central-bank): add health endpoint tests"
```

---

## Task B11: Write account endpoint tests

### Step 11.1: Write test_accounts.py

Create `services/central-bank/tests/unit/routers/test_accounts.py`:

```python
"""Account endpoint tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _setup_identity_mock_for_platform(
    app_state: Any,
    platform_keypair: Any,
    agent_exists: bool = True,
    agent_id: str = "a-test-agent",
) -> None:
    """Configure the mock identity client for platform operations."""
    private_key, _public_key = platform_keypair

    async def mock_verify_jws(token: str) -> dict[str, Any]:
        # Decode the token to extract payload (trust it for testing)
        import base64
        import json

        parts = token.split(".")
        header_b64 = parts[0]
        padding = 4 - len(header_b64) % 4
        if padding != 4:
            header_b64 += "=" * padding
        header = json.loads(base64.urlsafe_b64decode(header_b64))

        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        return {"valid": True, "agent_id": header["kid"], "payload": payload}

    app_state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)

    if agent_exists:
        app_state.identity_client.get_agent = AsyncMock(
            return_value={"agent_id": agent_id, "name": "Test Agent"}
        )
    else:
        app_state.identity_client.get_agent = AsyncMock(return_value=None)


@pytest.mark.unit
class TestCreateAccount:
    """Tests for POST /accounts."""

    async def test_create_account_success(self, client, platform_keypair):
        """Platform can create an account."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, platform_keypair)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": 50},
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 201
        data = response.json()
        assert data["account_id"] == "a-test-agent"
        assert data["balance"] == 50
        assert "created_at" in data

    async def test_create_account_zero_balance(self, client, platform_keypair):
        """Account can be created with zero initial balance."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, platform_keypair)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": 0},
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 201
        assert response.json()["balance"] == 0

    async def test_create_duplicate_account(self, client, platform_keypair):
        """Duplicate account returns 409."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, platform_keypair)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": 50},
        )

        await client.post("/accounts", json={"token": token})
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 409
        assert response.json()["error"] == "ACCOUNT_EXISTS"

    async def test_create_account_agent_not_found(self, client, platform_keypair):
        """Account for non-existent agent returns 404."""
        state = get_app_state()
        _setup_identity_mock_for_platform(
            state, platform_keypair, agent_exists=False
        )

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-nonexistent", "initial_balance": 0},
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 404
        assert response.json()["error"] == "AGENT_NOT_FOUND"

    async def test_create_account_non_platform_forbidden(self, client, agent_keypair):
        """Non-platform agent cannot create accounts."""
        state = get_app_state()
        private_key, _ = agent_keypair
        agent_id = "a-regular-agent"

        async def mock_verify_jws(token: str) -> dict[str, Any]:
            return {
                "valid": True,
                "agent_id": agent_id,
                "payload": {"action": "create_account", "agent_id": "a-victim"},
            }

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)

        token = make_jws_token(
            private_key,
            agent_id,
            {"action": "create_account", "agent_id": "a-victim", "initial_balance": 0},
        )

        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_create_account_missing_token(self, client):
        """Missing token returns 400."""
        response = await client.post("/accounts", json={})
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
class TestCreditAccount:
    """Tests for POST /accounts/{account_id}/credit."""

    async def test_credit_success(self, client, platform_keypair):
        """Platform can credit an account."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, platform_keypair)

        private_key, _ = platform_keypair

        # Create account first
        create_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test-agent", "initial_balance": 50},
        )
        await client.post("/accounts", json={"token": create_token})

        # Credit it
        credit_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-test-agent", "amount": 10, "reference": "salary_round_1"},
        )
        response = await client.post(
            "/accounts/a-test-agent/credit",
            json={"token": credit_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["balance_after"] == 60
        assert data["tx_id"].startswith("tx-")

    async def test_credit_account_not_found(self, client, platform_keypair):
        """Credit to non-existent account returns 404."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, platform_keypair)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "amount": 10, "reference": "test"},
        )
        response = await client.post(
            "/accounts/a-nonexistent/credit",
            json={"token": token},
        )
        assert response.status_code == 404
        assert response.json()["error"] == "ACCOUNT_NOT_FOUND"


@pytest.mark.unit
class TestGetBalance:
    """Tests for GET /accounts/{account_id}."""

    async def test_get_balance_success(self, client, platform_keypair, agent_keypair):
        """Agent can check own balance."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, platform_keypair, agent_id="a-alice")

        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair
        agent_id = "a-alice"

        # Create account as platform
        create_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": agent_id, "initial_balance": 100},
        )
        await client.post("/accounts", json={"token": create_token})

        # Agent reads own balance
        async def mock_verify_agent(token: str) -> dict[str, Any]:
            return {"valid": True, "agent_id": agent_id, "payload": {"action": "get_balance"}}

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_agent)

        balance_token = make_jws_token(
            agent_key, agent_id, {"action": "get_balance", "account_id": agent_id}
        )
        response = await client.get(
            f"/accounts/{agent_id}",
            headers={"Authorization": f"Bearer {balance_token}"},
        )
        assert response.status_code == 200
        assert response.json()["balance"] == 100

    async def test_get_balance_forbidden_other_account(self, client, platform_keypair, agent_keypair):
        """Agent cannot read another agent's balance."""
        state = get_app_state()

        async def mock_verify(token: str) -> dict[str, Any]:
            return {"valid": True, "agent_id": "a-eve", "payload": {"action": "get_balance"}}

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify)

        agent_key, _ = agent_keypair
        token = make_jws_token(agent_key, "a-eve", {"action": "get_balance"})
        response = await client.get(
            "/accounts/a-alice",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_get_balance_missing_auth_header(self, client):
        """Missing Authorization header returns 400."""
        response = await client.get("/accounts/a-test")
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
class TestGetTransactions:
    """Tests for GET /accounts/{account_id}/transactions."""

    async def test_get_transactions_success(self, client, platform_keypair, agent_keypair):
        """Agent can view own transaction history."""
        state = get_app_state()
        _setup_identity_mock_for_platform(state, platform_keypair, agent_id="a-alice")

        platform_key, _ = platform_keypair
        agent_key, _ = agent_keypair
        agent_id = "a-alice"

        # Create account with initial balance
        create_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": agent_id, "initial_balance": 50},
        )
        await client.post("/accounts", json={"token": create_token})

        # Agent reads own transactions
        async def mock_verify_agent(token: str) -> dict[str, Any]:
            return {"valid": True, "agent_id": agent_id, "payload": {"action": "get_transactions"}}

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_agent)

        tx_token = make_jws_token(
            agent_key, agent_id, {"action": "get_transactions", "account_id": agent_id}
        )
        response = await client.get(
            f"/accounts/{agent_id}/transactions",
            headers={"Authorization": f"Bearer {tx_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "transactions" in data
        assert len(data["transactions"]) == 1
        assert data["transactions"][0]["type"] == "credit"
        assert data["transactions"][0]["amount"] == 50
```

### Step 11.2: Run tests

```bash
cd services/central-bank && uv run pytest tests/unit/routers/test_accounts.py -v
```

Expected: PASS

### Step 11.3: Commit

```bash
git add services/central-bank/tests/unit/routers/test_accounts.py
git commit -m "test(central-bank): add account endpoint tests"
```

---

## Task B12: Write escrow endpoint tests

### Step 12.1: Write test_escrow.py

Create `services/central-bank/tests/unit/routers/test_escrow.py`:

```python
"""Escrow endpoint tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _setup_identity_mock(state: Any, platform_keypair: Any) -> None:
    """Configure mock identity client that decodes tokens."""

    async def mock_verify_jws(token: str) -> dict[str, Any]:
        import base64
        import json

        parts = token.split(".")
        header_b64 = parts[0]
        padding = 4 - len(header_b64) % 4
        if padding != 4:
            header_b64 += "=" * padding
        header = json.loads(base64.urlsafe_b64decode(header_b64))

        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))

        return {"valid": True, "agent_id": header["kid"], "payload": payload}

    state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)
    state.identity_client.get_agent = AsyncMock(
        return_value={"agent_id": "a-test-agent", "name": "Test"}
    )


async def _create_funded_account(
    client: Any,
    state: Any,
    platform_keypair: Any,
    account_id: str,
    balance: int,
) -> None:
    """Helper to create a funded account via the API."""
    private_key, _ = platform_keypair
    token = make_jws_token(
        private_key,
        PLATFORM_AGENT_ID,
        {"action": "create_account", "agent_id": account_id, "initial_balance": balance},
    )
    resp = await client.post("/accounts", json={"token": token})
    assert resp.status_code == 201


@pytest.mark.unit
class TestEscrowLock:
    """Tests for POST /escrow/lock."""

    async def test_escrow_lock_success(self, client, platform_keypair, agent_keypair):
        """Agent can lock own funds in escrow."""
        state = get_app_state()
        _setup_identity_mock(state, platform_keypair)

        await _create_funded_account(client, state, platform_keypair, "a-payer", 100)

        agent_key, _ = agent_keypair
        token = make_jws_token(
            agent_key,
            "a-payer",
            {"action": "escrow_lock", "agent_id": "a-payer", "amount": 30, "task_id": "T-001"},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 201
        data = response.json()
        assert data["escrow_id"].startswith("esc-")
        assert data["amount"] == 30
        assert data["task_id"] == "T-001"
        assert data["status"] == "locked"

    async def test_escrow_lock_insufficient_funds(self, client, platform_keypair, agent_keypair):
        """Escrow lock with insufficient funds returns 402."""
        state = get_app_state()
        _setup_identity_mock(state, platform_keypair)

        await _create_funded_account(client, state, platform_keypair, "a-payer", 10)

        agent_key, _ = agent_keypair
        token = make_jws_token(
            agent_key,
            "a-payer",
            {"action": "escrow_lock", "agent_id": "a-payer", "amount": 100, "task_id": "T-001"},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 402
        assert response.json()["error"] == "INSUFFICIENT_FUNDS"

    async def test_escrow_lock_wrong_agent_forbidden(self, client, platform_keypair, agent_keypair):
        """Agent cannot lock another agent's funds."""
        state = get_app_state()
        _setup_identity_mock(state, platform_keypair)

        await _create_funded_account(client, state, platform_keypair, "a-victim", 100)

        agent_key, _ = agent_keypair
        # Eve tries to lock victim's funds but signs as a-eve
        async def mock_verify_eve(token: str) -> dict[str, Any]:
            return {
                "valid": True,
                "agent_id": "a-eve",
                "payload": {"action": "escrow_lock", "agent_id": "a-victim", "amount": 50, "task_id": "T-001"},
            }

        state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_eve)

        token = make_jws_token(
            agent_key,
            "a-eve",
            {"action": "escrow_lock", "agent_id": "a-victim", "amount": 50, "task_id": "T-001"},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
class TestEscrowRelease:
    """Tests for POST /escrow/{escrow_id}/release."""

    async def test_escrow_release_success(self, client, platform_keypair, agent_keypair):
        """Platform can release escrowed funds to recipient."""
        state = get_app_state()
        _setup_identity_mock(state, platform_keypair)

        await _create_funded_account(client, state, platform_keypair, "a-payer", 100)
        await _create_funded_account(client, state, platform_keypair, "a-worker", 0)

        # Lock funds
        agent_key, _ = agent_keypair
        lock_token = make_jws_token(
            agent_key,
            "a-payer",
            {"action": "escrow_lock", "agent_id": "a-payer", "amount": 30, "task_id": "T-001"},
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        escrow_id = lock_resp.json()["escrow_id"]

        # Release as platform
        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {"action": "escrow_release", "escrow_id": escrow_id, "recipient_account_id": "a-worker"},
        )
        response = await client.post(
            f"/escrow/{escrow_id}/release",
            json={"token": release_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "released"
        assert data["amount"] == 30
        assert data["recipient"] == "a-worker"

    async def test_escrow_release_already_resolved(self, client, platform_keypair, agent_keypair):
        """Releasing already-resolved escrow returns 409."""
        state = get_app_state()
        _setup_identity_mock(state, platform_keypair)

        await _create_funded_account(client, state, platform_keypair, "a-payer", 100)
        await _create_funded_account(client, state, platform_keypair, "a-worker", 0)

        agent_key, _ = agent_keypair
        lock_token = make_jws_token(
            agent_key,
            "a-payer",
            {"action": "escrow_lock", "agent_id": "a-payer", "amount": 30, "task_id": "T-001"},
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        escrow_id = lock_resp.json()["escrow_id"]

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {"action": "escrow_release", "escrow_id": escrow_id, "recipient_account_id": "a-worker"},
        )
        await client.post(f"/escrow/{escrow_id}/release", json={"token": release_token})

        # Try to release again
        response = await client.post(f"/escrow/{escrow_id}/release", json={"token": release_token})
        assert response.status_code == 409
        assert response.json()["error"] == "ESCROW_ALREADY_RESOLVED"

    async def test_escrow_not_found(self, client, platform_keypair):
        """Release of non-existent escrow returns 404."""
        state = get_app_state()
        _setup_identity_mock(state, platform_keypair)

        platform_key, _ = platform_keypair
        release_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {"action": "escrow_release", "escrow_id": "esc-fake", "recipient_account_id": "a-worker"},
        )
        response = await client.post(
            "/escrow/esc-fake/release",
            json={"token": release_token},
        )
        assert response.status_code == 404
        assert response.json()["error"] == "ESCROW_NOT_FOUND"


@pytest.mark.unit
class TestEscrowSplit:
    """Tests for POST /escrow/{escrow_id}/split."""

    async def test_escrow_split_success(self, client, platform_keypair, agent_keypair):
        """Platform can split escrowed funds between worker and poster."""
        state = get_app_state()
        _setup_identity_mock(state, platform_keypair)

        await _create_funded_account(client, state, platform_keypair, "a-poster", 100)
        await _create_funded_account(client, state, platform_keypair, "a-worker", 0)

        # Lock funds
        agent_key, _ = agent_keypair
        lock_token = make_jws_token(
            agent_key,
            "a-poster",
            {"action": "escrow_lock", "agent_id": "a-poster", "amount": 100, "task_id": "T-001"},
        )
        lock_resp = await client.post("/escrow/lock", json={"token": lock_token})
        escrow_id = lock_resp.json()["escrow_id"]

        # Split as platform: 40% to worker, 60% back to poster
        platform_key, _ = platform_keypair
        split_token = make_jws_token(
            platform_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_split",
                "escrow_id": escrow_id,
                "worker_account_id": "a-worker",
                "worker_pct": 40,
                "poster_account_id": "a-poster",
            },
        )
        response = await client.post(
            f"/escrow/{escrow_id}/split",
            json={"token": split_token},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "split"
        assert data["worker_amount"] == 40
        assert data["poster_amount"] == 60
```

### Step 12.2: Run tests

```bash
cd services/central-bank && uv run pytest tests/unit/routers/test_escrow.py -v
```

Expected: PASS

### Step 12.3: Commit

```bash
git add services/central-bank/tests/unit/routers/test_escrow.py
git commit -m "test(central-bank): add escrow endpoint tests"
```

---

## Task B13: Add integration and performance test placeholders

### Step 13.1: Write placeholders

Create `services/central-bank/tests/integration/conftest.py`:

```python
"""Integration test configuration."""
```

Create `services/central-bank/tests/integration/test_endpoints.py`:

```python
"""Integration tests that require running Identity service."""

import pytest


@pytest.mark.integration
def test_placeholder():
    """Placeholder for integration tests that require a running service."""
    pytest.skip("Integration tests require a running Identity service")
```

Create `services/central-bank/tests/performance/conftest.py`:

```python
"""Performance test configuration."""
```

Create `services/central-bank/tests/performance/test_performance.py`:

```python
"""Performance benchmark tests."""

import pytest


@pytest.mark.performance
def test_placeholder():
    """Placeholder for performance benchmarks."""
    pytest.skip("Performance tests not yet implemented")
```

### Step 13.2: Commit

```bash
git add services/central-bank/tests/integration/ services/central-bank/tests/performance/
git commit -m "test(central-bank): add integration and performance test placeholders"
```

---

## Verification

```bash
cd services/central-bank && just ci-quiet
```

All CI checks must pass.

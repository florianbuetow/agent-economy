# Phase 7 — Tests

## Working Directory

All paths relative to `services/identity/`.

---

## File 1: `tests/conftest.py`

Create this file. This provides shared test configuration used by all test types.

```python
"""Shared test configuration."""
```

---

## File 2: `tests/unit/conftest.py`

Create this file:

```python
"""Unit test fixtures — cache clearing."""

import pytest

from identity_service.config import clear_settings_cache
from identity_service.core.state import reset_app_state


@pytest.fixture(autouse=True)
def _clear_caches():
    """Clear settings cache and app state between tests."""
    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()
```

---

## File 3: `tests/unit/test_config.py`

Create this file:

```python
"""Unit tests for configuration loading."""

import os

import pytest

from identity_service.config import Settings, clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path):
    """Config loads correctly from a valid YAML file."""
    config_content = """
service:
  name: "identity"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8001
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/test.db"
crypto:
  algorithm: "ed25519"
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.service.name == "identity"
    assert settings.database.path == "data/test.db"
    assert settings.crypto.public_key_bytes == 32
    assert settings.request.max_body_size == 1572864

    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path):
    """Config with extra fields causes validation error."""
    config_content = """
service:
  name: "identity"
  version: "0.1.0"
  extra_field: "should fail"
server:
  host: "0.0.0.0"
  port: 8001
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/test.db"
crypto:
  algorithm: "ed25519"
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    with pytest.raises(Exception):  # noqa: B017
        get_settings()

    os.environ.pop("CONFIG_PATH", None)
```

---

## File 4: `tests/unit/routers/__init__.py`

Create this empty file:

```python
```

---

## File 5: `tests/unit/routers/conftest.py`

Create this file. Provides the test app and HTTP client fixtures.

```python
"""Router test fixtures — app with lifespan and async client."""

import os

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app(tmp_path):
    """Create a test app with a temporary database."""
    db_path = tmp_path / "test.db"
    config_content = f"""
service:
  name: "identity"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8001
  log_level: "info"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "{db_path}"
crypto:
  algorithm: "ed25519"
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    from identity_service.config import clear_settings_cache
    from identity_service.core.state import reset_app_state

    clear_settings_cache()
    reset_app_state()

    from identity_service.app import create_app
    from identity_service.core.lifespan import lifespan

    test_app = create_app()
    async with lifespan(test_app):
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

---

## File 6: `tests/unit/routers/test_health.py`

Create this file:

```python
"""Unit tests for health endpoint."""

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
    assert data["registered_agents"] == 0


@pytest.mark.unit
async def test_health_post_not_allowed(client):
    """POST /health returns 405."""
    response = await client.post("/health")
    assert response.status_code == 405
    assert response.json()["error"] == "METHOD_NOT_ALLOWED"
```

---

## File 7: `tests/unit/routers/test_agents.py`

Create this file:

```python
"""Unit tests for agent endpoints."""

import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


@pytest.mark.unit
async def test_register_valid_agent(client):
    """POST /agents/register with valid data returns 201."""
    _, public_key = _generate_keypair()
    response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    assert response.status_code == 201

    data = response.json()
    assert data["name"] == "Alice"
    assert data["public_key"] == public_key
    assert data["agent_id"].startswith("a-")
    assert "registered_at" in data


@pytest.mark.unit
async def test_register_missing_name(client):
    """POST /agents/register without name returns 400 MISSING_FIELD."""
    _, public_key = _generate_keypair()
    response = await client.post(
        "/agents/register",
        json={"public_key": public_key},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "MISSING_FIELD"


@pytest.mark.unit
async def test_register_duplicate_key(client):
    """POST /agents/register with duplicate key returns 409."""
    _, public_key = _generate_keypair()
    response1 = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    assert response1.status_code == 201

    response2 = await client.post(
        "/agents/register",
        json={"name": "Eve", "public_key": public_key},
    )
    assert response2.status_code == 409
    assert response2.json()["error"] == "PUBLIC_KEY_EXISTS"


@pytest.mark.unit
async def test_get_agent(client):
    """GET /agents/{agent_id} returns the agent record."""
    _, public_key = _generate_keypair()
    reg_response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    agent_id = reg_response.json()["agent_id"]

    response = await client.get(f"/agents/{agent_id}")
    assert response.status_code == 200
    assert response.json()["agent_id"] == agent_id
    assert response.json()["public_key"] == public_key


@pytest.mark.unit
async def test_get_agent_not_found(client):
    """GET /agents/{agent_id} with unknown ID returns 404."""
    response = await client.get("/agents/a-00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"] == "AGENT_NOT_FOUND"


@pytest.mark.unit
async def test_list_agents_empty(client):
    """GET /agents on fresh system returns empty list."""
    response = await client.get("/agents")
    assert response.status_code == 200
    assert response.json() == {"agents": []}


@pytest.mark.unit
async def test_list_agents_omits_public_key(client):
    """GET /agents does not include public_key in items."""
    _, public_key = _generate_keypair()
    await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )

    response = await client.get("/agents")
    assert response.status_code == 200
    agents = response.json()["agents"]
    assert len(agents) == 1
    assert "public_key" not in agents[0]


@pytest.mark.unit
async def test_verify_valid_signature(client):
    """POST /agents/verify with correct signature returns valid=true."""
    private_key, public_key = _generate_keypair()
    reg_response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key},
    )
    agent_id = reg_response.json()["agent_id"]

    payload = b"hello world"
    payload_b64 = base64.b64encode(payload).decode()
    signature = private_key.sign(payload)
    signature_b64 = base64.b64encode(signature).decode()

    response = await client.post(
        "/agents/verify",
        json={
            "agent_id": agent_id,
            "payload": payload_b64,
            "signature": signature_b64,
        },
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert response.json()["agent_id"] == agent_id


@pytest.mark.unit
async def test_verify_wrong_signature(client):
    """POST /agents/verify with wrong signature returns valid=false."""
    private_key_a, public_key_a = _generate_keypair()
    private_key_b, _ = _generate_keypair()

    reg_response = await client.post(
        "/agents/register",
        json={"name": "Alice", "public_key": public_key_a},
    )
    agent_id = reg_response.json()["agent_id"]

    payload = b"hello world"
    payload_b64 = base64.b64encode(payload).decode()
    wrong_sig = private_key_b.sign(payload)
    signature_b64 = base64.b64encode(wrong_sig).decode()

    response = await client.post(
        "/agents/verify",
        json={
            "agent_id": agent_id,
            "payload": payload_b64,
            "signature": signature_b64,
        },
    )
    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["reason"] == "signature mismatch"


@pytest.mark.unit
async def test_register_invalid_json(client):
    """POST /agents/register with broken JSON returns 400 INVALID_JSON."""
    response = await client.post(
        "/agents/register",
        content=b"{broken",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_JSON"


@pytest.mark.unit
async def test_register_wrong_content_type(client):
    """POST /agents/register with text/plain returns 415."""
    response = await client.post(
        "/agents/register",
        content=b'{"name":"Alice"}',
        headers={"Content-Type": "text/plain"},
    )
    assert response.status_code == 415
    assert response.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.unit
async def test_method_not_allowed_register(client):
    """GET /agents/register returns 405."""
    response = await client.get("/agents/register")
    assert response.status_code == 405
    assert response.json()["error"] == "METHOD_NOT_ALLOWED"
```

---

## File 8: `tests/integration/conftest.py`

Create this file:

```python
"""Integration test fixtures."""
```

---

## File 9: `tests/integration/test_endpoints.py`

Create this file:

```python
"""Integration tests — require running service."""

import pytest


@pytest.mark.integration
def test_placeholder():
    """Placeholder for integration tests that require a running service."""
    pytest.skip("Integration tests require a running service")
```

---

## File 10: `tests/performance/conftest.py`

Create this file:

```python
"""Performance test fixtures."""
```

---

## File 11: `tests/performance/test_performance.py`

Create this file:

```python
"""Performance benchmark tests."""

import pytest


@pytest.mark.performance
def test_placeholder():
    """Placeholder for performance benchmarks."""
    pytest.skip("Performance tests not yet implemented")
```

---

## Create missing directory

The `tests/unit/routers/` directory may not exist yet:

```bash
cd services/identity && mkdir -p tests/unit/routers
```

---

## Verification

```bash
cd services/identity && just ci-quiet
```

All CI checks must pass. If unit tests fail, check:
1. Are all files in the correct locations?
2. Did the `tests/unit/routers/__init__.py` file get created?
3. Do the test fixtures properly set `CONFIG_PATH` and clear caches?

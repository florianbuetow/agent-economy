# Phase 8 — Tests

## Working Directory

All paths relative to `services/task-board/`.

---

## Overview

The test specification defines 171 tests across 17 categories. All tests are pytest-based, use async fixtures, and mock the Identity and Central Bank services via conftest fixtures. No external services are required.

This phase creates:
1. Complete conftest.py files with all fixtures and helpers
2. Representative tests from each category showing the pattern
3. Instructions for implementing the remaining tests

---

## Create directories

```bash
cd services/task-board && mkdir -p tests/unit/routers
```

---

## File 1: `tests/conftest.py`

Create this file:

```python
"""Shared test configuration."""
```

---

## File 2: `tests/unit/conftest.py`

Create this file. This provides all shared fixtures: JWS helpers, Identity mock, Central Bank mock, app factory, HTTP client, and multi-step helper functions.

```python
"""Unit test fixtures — app, client, mocks, and helpers."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from httpx import ASGITransport, AsyncClient
from joserfc import jws as jws_module
from joserfc.jwk import OKPKey


# ---------------------------------------------------------------------------
# JWS helpers
# ---------------------------------------------------------------------------


@dataclass
class AgentKeypair:
    """An agent's identity material for testing."""

    agent_id: str
    private_key: Ed25519PrivateKey
    okp_key: OKPKey
    public_key_formatted: str


def make_keypair() -> AgentKeypair:
    """Generate a fresh Ed25519 keypair and agent ID."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    priv_bytes = private_key.private_bytes(
        Encoding.Raw,
        format=__import__("cryptography.hazmat.primitives.serialization", fromlist=["PrivateFormat"]).PrivateFormat.Raw,
        encryption_algorithm=__import__("cryptography.hazmat.primitives.serialization", fromlist=["NoEncryption"]).NoEncryption(),
    )
    agent_id = f"a-{uuid.uuid4()}"
    okp_key = OKPKey.import_key(
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": base64.urlsafe_b64encode(priv_bytes).rstrip(b"=").decode(),
            "x": base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode(),
        }
    )
    public_key_formatted = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return AgentKeypair(
        agent_id=agent_id,
        private_key=private_key,
        okp_key=okp_key,
        public_key_formatted=public_key_formatted,
    )


def make_jws(keypair: AgentKeypair, payload: dict[str, Any]) -> str:
    """Create a valid JWS compact token signed by the given agent."""
    header = {"alg": "EdDSA", "kid": keypair.agent_id}
    payload_bytes = json.dumps(payload).encode()
    token = jws_module.serialize_compact(header, payload_bytes, keypair.okp_key)
    return token


def make_tampered_jws(keypair: AgentKeypair, payload: dict[str, Any]) -> str:
    """Create a JWS, then alter the payload so the signature no longer matches."""
    token = make_jws(keypair, payload)
    parts = token.split(".")
    # Decode payload, alter it, re-encode
    padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
    original = json.loads(base64.urlsafe_b64decode(padded))
    original["_tampered"] = True
    new_payload = base64.urlsafe_b64encode(json.dumps(original).encode()).rstrip(b"=").decode()
    return f"{parts[0]}.{new_payload}.{parts[2]}"


# ---------------------------------------------------------------------------
# Identity service mock
# ---------------------------------------------------------------------------


@dataclass
class IdentityMockState:
    """Tracks calls and controls behavior of the Identity mock."""

    verify_calls: list[dict[str, Any]] = field(default_factory=list)
    force_invalid: bool = False
    force_unavailable: bool = False


async def identity_mock_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI app that mocks POST /agents/verify-jws."""
    if scope["type"] != "http":
        return

    state: IdentityMockState = scope["app"].state  # type: ignore[attr-defined]

    body_parts: list[bytes] = []
    while True:
        message = await receive()
        body_parts.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    body = b"".join(body_parts)

    path = scope["path"]
    method = scope["method"]

    if method == "POST" and path == "/agents/verify-jws":
        if state.force_unavailable:
            # Simulate service down by returning 500
            await _send_json(send, 500, {"error": "INTERNAL"})
            return

        request_data = json.loads(body)
        state.verify_calls.append(request_data)
        token = request_data.get("token", "")

        if state.force_invalid:
            await _send_json(send, 200, {"valid": False, "reason": "forced invalid"})
            return

        # Decode the JWS to extract kid and payload
        try:
            parts = token.split(".")
            if len(parts) != 3:
                await _send_json(send, 200, {"valid": False, "reason": "malformed jws"})
                return

            header_padded = parts[0] + "=" * (4 - len(parts[0]) % 4)
            header = json.loads(base64.urlsafe_b64decode(header_padded))
            kid = header.get("kid", "")

            payload_padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_padded))

            # Verify signature using the registered key (we trust test tokens)
            # In tests, we accept the token as valid unless force_invalid is set
            await _send_json(send, 200, {
                "valid": True,
                "agent_id": kid,
                "payload": payload,
            })
        except Exception:
            await _send_json(send, 200, {"valid": False, "reason": "decode error"})
    else:
        await _send_json(send, 404, {"error": "NOT_FOUND"})


async def _send_json(send: Any, status: int, body: dict[str, Any]) -> None:
    """Send an HTTP JSON response via raw ASGI."""
    body_bytes = json.dumps(body).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body_bytes)).encode()],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body_bytes,
    })


# ---------------------------------------------------------------------------
# Central Bank mock
# ---------------------------------------------------------------------------


@dataclass
class CentralBankMockState:
    """Tracks calls and controls behavior of the Central Bank mock."""

    lock_calls: list[dict[str, Any]] = field(default_factory=list)
    release_calls: list[dict[str, Any]] = field(default_factory=list)
    force_insufficient_funds: bool = False
    force_unavailable: bool = False
    force_release_error: bool = False


async def central_bank_mock_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Minimal ASGI app that mocks Central Bank escrow endpoints."""
    if scope["type"] != "http":
        return

    state: CentralBankMockState = scope["app"].state  # type: ignore[attr-defined]

    body_parts: list[bytes] = []
    while True:
        message = await receive()
        body_parts.append(message.get("body", b""))
        if not message.get("more_body", False):
            break
    body = b"".join(body_parts)

    path = scope["path"]
    method = scope["method"]

    if state.force_unavailable:
        await _send_json(send, 500, {"error": "INTERNAL"})
        return

    if method == "POST" and path == "/escrow/lock":
        request_data = json.loads(body) if body else {}
        state.lock_calls.append(request_data)

        if state.force_insufficient_funds:
            await _send_json(send, 402, {
                "error": "INSUFFICIENT_FUNDS",
                "message": "Not enough balance",
                "details": {},
            })
            return

        # Decode the escrow token payload to get task_id and amount
        token = request_data.get("token", "")
        task_id = "unknown"
        amount = 0
        try:
            parts = token.split(".")
            if len(parts) == 3:
                payload_padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
                payload = json.loads(base64.urlsafe_b64decode(payload_padded))
                task_id = payload.get("task_id", "unknown")
                amount = payload.get("amount", 0)
        except Exception:
            pass

        escrow_id = f"esc-{uuid.uuid4()}"
        await _send_json(send, 200, {
            "escrow_id": escrow_id,
            "amount": amount,
            "task_id": task_id,
            "status": "locked",
        })

    elif method == "POST" and "/escrow/" in path and path.endswith("/release"):
        request_data = json.loads(body) if body else {}
        state.release_calls.append(request_data)

        if state.force_release_error:
            await _send_json(send, 500, {"error": "INTERNAL"})
            return

        # Extract escrow_id from path: /escrow/{escrow_id}/release
        parts = path.split("/")
        escrow_id = parts[2] if len(parts) >= 4 else "unknown"
        await _send_json(send, 200, {
            "escrow_id": escrow_id,
            "status": "released",
        })
    else:
        await _send_json(send, 404, {"error": "NOT_FOUND"})


# ---------------------------------------------------------------------------
# Mock server wrappers (used by httpx)
# ---------------------------------------------------------------------------


class MockASGIApp:
    """Wrapper that attaches state to an ASGI callable."""

    def __init__(self, handler: Any, state: Any) -> None:
        self.handler = handler
        self.state = state

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        scope["app"] = self
        await self.handler(scope, receive, send)


# ---------------------------------------------------------------------------
# App + client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    """Clear settings cache and app state between tests."""
    from task_board_service.config import clear_settings_cache
    from task_board_service.core.state import reset_app_state

    clear_settings_cache()
    reset_app_state()
    yield
    clear_settings_cache()
    reset_app_state()


@pytest.fixture
def identity_mock_state() -> IdentityMockState:
    """Controllable Identity mock state."""
    return IdentityMockState()


@pytest.fixture
def central_bank_mock_state() -> CentralBankMockState:
    """Controllable Central Bank mock state."""
    return CentralBankMockState()


@pytest.fixture
def alice() -> AgentKeypair:
    """Agent Alice keypair."""
    return make_keypair()


@pytest.fixture
def bob() -> AgentKeypair:
    """Agent Bob keypair."""
    return make_keypair()


@pytest.fixture
def carol() -> AgentKeypair:
    """Agent Carol keypair."""
    return make_keypair()


@pytest.fixture
def platform_agent() -> AgentKeypair:
    """Platform agent keypair."""
    return make_keypair()


@pytest.fixture
async def app(
    tmp_path: Any,
    identity_mock_state: IdentityMockState,
    central_bank_mock_state: CentralBankMockState,
    platform_agent: AgentKeypair,
) -> Any:
    """Create a test app with temporary database, asset storage, and mock services."""
    db_path = tmp_path / "test.db"
    asset_path = tmp_path / "assets"
    asset_path.mkdir()

    # Write platform private key to file
    from cryptography.hazmat.primitives.serialization import NoEncryption, PrivateFormat

    priv_bytes = platform_agent.private_key.private_bytes(
        Encoding.Raw, PrivateFormat.Raw, NoEncryption()
    )
    key_path = tmp_path / "platform.key"
    key_path.write_bytes(priv_bytes)

    # Create mock servers that httpx can transport to
    identity_app = MockASGIApp(identity_mock_app, identity_mock_state)
    bank_app = MockASGIApp(central_bank_mock_app, central_bank_mock_state)

    # Start real HTTP servers for the mocks on random ports
    import uvicorn

    identity_server = uvicorn.Server(
        uvicorn.Config(identity_app, host="127.0.0.1", port=0, log_level="error")
    )
    bank_server = uvicorn.Server(
        uvicorn.Config(bank_app, host="127.0.0.1", port=0, log_level="error")
    )

    identity_task = asyncio.create_task(identity_server.serve())
    bank_task = asyncio.create_task(bank_server.serve())

    # Wait for servers to start
    for _ in range(100):
        if identity_server.started and bank_server.started:
            break
        await asyncio.sleep(0.05)

    identity_port = identity_server.servers[0].sockets[0].getsockname()[1]
    bank_port = bank_server.servers[0].sockets[0].getsockname()[1]

    config_content = f"""
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8003
  log_level: "warning"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "{db_path}"
identity:
  base_url: "http://127.0.0.1:{identity_port}"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 5
central_bank:
  base_url: "http://127.0.0.1:{bank_port}"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/{{escrow_id}}/release"
  timeout_seconds: 5
platform:
  agent_id: "{platform_agent.agent_id}"
  private_key_path: "{key_path}"
assets:
  storage_path: "{asset_path}"
  max_file_size: 10485760
  max_files_per_task: 10
request:
  max_body_size: 10485760
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    from task_board_service.config import clear_settings_cache
    from task_board_service.core.state import reset_app_state

    clear_settings_cache()
    reset_app_state()

    from task_board_service.app import create_app
    from task_board_service.core.lifespan import lifespan

    test_app = create_app()
    async with lifespan(test_app):
        yield test_app

    reset_app_state()
    clear_settings_cache()
    os.environ.pop("CONFIG_PATH", None)

    # Shutdown mock servers
    identity_server.should_exit = True
    bank_server.should_exit = True
    await asyncio.sleep(0.1)
    identity_task.cancel()
    bank_task.cancel()
    try:
        await identity_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await bank_task
    except (asyncio.CancelledError, Exception):
        pass


@pytest.fixture
async def client(app: Any) -> Any:
    """Async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Multi-step helper functions
# ---------------------------------------------------------------------------


def _make_task_id() -> str:
    """Generate a valid task ID."""
    return f"t-{uuid.uuid4()}"


async def create_task(
    client: AsyncClient,
    poster: AgentKeypair,
    *,
    task_id: str | None = None,
    title: str = "Test task",
    spec: str = "Implement the feature as described.",
    reward: int = 100,
    bidding_deadline_seconds: int = 86400,
    deadline_seconds: int = 3600,
    review_deadline_seconds: int = 600,
) -> dict[str, Any]:
    """Create a task and assert 201. Returns the response JSON."""
    if task_id is None:
        task_id = _make_task_id()

    task_payload = {
        "action": "create_task",
        "task_id": task_id,
        "poster_id": poster.agent_id,
        "title": title,
        "spec": spec,
        "reward": reward,
        "bidding_deadline_seconds": bidding_deadline_seconds,
        "deadline_seconds": deadline_seconds,
        "review_deadline_seconds": review_deadline_seconds,
    }
    escrow_payload = {
        "action": "escrow_lock",
        "agent_id": poster.agent_id,
        "amount": reward,
        "task_id": task_id,
    }
    task_token = make_jws(poster, task_payload)
    escrow_token = make_jws(poster, escrow_payload)

    response = await client.post(
        "/tasks",
        json={"task_token": task_token, "escrow_token": escrow_token},
    )
    assert response.status_code == 201, f"create_task failed: {response.status_code} {response.text}"
    return response.json()


async def submit_bid(
    client: AsyncClient,
    bidder: AgentKeypair,
    task_id: str,
    *,
    proposal: str = "I will implement this using best practices.",
) -> dict[str, Any]:
    """Submit a bid and assert 201. Returns the response JSON."""
    payload = {
        "action": "submit_bid",
        "task_id": task_id,
        "bidder_id": bidder.agent_id,
        "proposal": proposal,
    }
    token = make_jws(bidder, payload)
    response = await client.post(
        f"/tasks/{task_id}/bids",
        json={"token": token},
    )
    assert response.status_code == 201, f"submit_bid failed: {response.status_code} {response.text}"
    return response.json()


async def accept_bid(
    client: AsyncClient,
    poster: AgentKeypair,
    task_id: str,
    bid_id: str,
) -> dict[str, Any]:
    """Accept a bid and assert 200. Returns the response JSON."""
    payload = {
        "action": "accept_bid",
        "task_id": task_id,
        "bid_id": bid_id,
        "poster_id": poster.agent_id,
    }
    token = make_jws(poster, payload)
    response = await client.post(
        f"/tasks/{task_id}/bids/{bid_id}/accept",
        json={"token": token},
    )
    assert response.status_code == 200, f"accept_bid failed: {response.status_code} {response.text}"
    return response.json()


async def upload_asset(
    client: AsyncClient,
    worker: AgentKeypair,
    task_id: str,
    *,
    filename: str = "deliverable.zip",
    content: bytes = b"test file content",
    content_type: str = "application/zip",
) -> dict[str, Any]:
    """Upload an asset and assert 201. Returns the response JSON."""
    payload = {
        "action": "upload_asset",
        "task_id": task_id,
        "worker_id": worker.agent_id,
    }
    token = make_jws(worker, payload)
    response = await client.post(
        f"/tasks/{task_id}/assets",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, content_type)},
    )
    assert response.status_code == 201, f"upload_asset failed: {response.status_code} {response.text}"
    return response.json()


async def submit_deliverable(
    client: AsyncClient,
    worker: AgentKeypair,
    task_id: str,
) -> dict[str, Any]:
    """Submit deliverable and assert 200. Returns the response JSON."""
    payload = {
        "action": "submit_deliverable",
        "task_id": task_id,
        "worker_id": worker.agent_id,
    }
    token = make_jws(worker, payload)
    response = await client.post(
        f"/tasks/{task_id}/submit",
        json={"token": token},
    )
    assert response.status_code == 200, f"submit_deliverable failed: {response.status_code} {response.text}"
    return response.json()


async def full_lifecycle_to_submitted(
    client: AsyncClient,
    poster: AgentKeypair,
    worker: AgentKeypair,
    *,
    task_id: str | None = None,
    review_deadline_seconds: int = 600,
) -> dict[str, Any]:
    """Execute full lifecycle through to SUBMITTED status. Returns the task JSON."""
    task = await create_task(
        client, poster, task_id=task_id, review_deadline_seconds=review_deadline_seconds
    )
    tid = task["task_id"]
    bid = await submit_bid(client, worker, tid)
    await accept_bid(client, poster, tid, bid["bid_id"])
    await upload_asset(client, worker, tid)
    result = await submit_deliverable(client, worker, tid)
    return result


async def full_lifecycle_to_disputed(
    client: AsyncClient,
    poster: AgentKeypair,
    worker: AgentKeypair,
    *,
    task_id: str | None = None,
    reason: str = "The deliverable does not meet requirements.",
) -> dict[str, Any]:
    """Execute full lifecycle through to DISPUTED status. Returns the task JSON."""
    submitted = await full_lifecycle_to_submitted(client, poster, worker, task_id=task_id)
    tid = submitted["task_id"]
    payload = {
        "action": "dispute_task",
        "task_id": tid,
        "poster_id": poster.agent_id,
        "reason": reason,
    }
    token = make_jws(poster, payload)
    response = await client.post(f"/tasks/{tid}/dispute", json={"token": token})
    assert response.status_code == 200, f"dispute failed: {response.status_code} {response.text}"
    return response.json()
```

---

## File 3: `tests/unit/test_config.py`

Create this file:

```python
"""Unit tests for configuration loading."""

import os

import pytest

from task_board_service.config import Settings, clear_settings_cache, get_settings


@pytest.mark.unit
def test_config_loads_from_yaml(tmp_path):
    """Config loads correctly from a valid YAML file."""
    config_content = """
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8003
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/test.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/{escrow_id}/release"
  timeout_seconds: 10
platform:
  agent_id: "a-test"
  private_key_path: "/tmp/test.key"
assets:
  storage_path: "data/assets"
  max_file_size: 10485760
  max_files_per_task: 10
request:
  max_body_size: 10485760
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    settings = get_settings()

    assert isinstance(settings, Settings)
    assert settings.service.name == "task-board"
    assert settings.database.path == "data/test.db"
    assert settings.identity.base_url == "http://localhost:8001"
    assert settings.central_bank.timeout_seconds == 10
    assert settings.assets.max_file_size == 10485760
    assert settings.request.max_body_size == 10485760

    os.environ.pop("CONFIG_PATH", None)


@pytest.mark.unit
def test_config_rejects_extra_fields(tmp_path):
    """Config with extra fields causes validation error."""
    config_content = """
service:
  name: "task-board"
  version: "0.1.0"
  extra_field: "should fail"
server:
  host: "0.0.0.0"
  port: 8003
  log_level: "info"
logging:
  level: "INFO"
  format: "json"
database:
  path: "data/test.db"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/{escrow_id}/release"
  timeout_seconds: 10
platform:
  agent_id: "a-test"
  private_key_path: "/tmp/test.key"
assets:
  storage_path: "data/assets"
  max_file_size: 10485760
  max_files_per_task: 10
request:
  max_body_size: 10485760
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

## File 4: `tests/unit/test_clients.py`

Create this file:

```python
"""Unit tests for HTTP clients."""

import pytest


@pytest.mark.unit
def test_placeholder_clients():
    """Placeholder — client tests are covered via router integration."""
    pass
```

---

## File 5: `tests/unit/test_task_manager.py`

Create this file:

```python
"""Unit tests for task manager service layer."""

import pytest


@pytest.mark.unit
def test_placeholder_task_manager():
    """Placeholder — service layer is tested via router tests."""
    pass
```

---

## File 6: `tests/unit/routers/__init__.py`

Create this empty file:

```python
```

---

## File 7: `tests/unit/routers/conftest.py`

Create this file:

```python
"""Router test fixtures — re-exports from parent conftest."""
```

---

## File 8: `tests/unit/routers/test_health.py`

Create this file. Covers **Category 14: HEALTH-01 to HEALTH-04**.

```python
"""Unit tests for health endpoint — Category 14."""

import asyncio

import pytest


@pytest.mark.unit
async def test_health_01_schema(client):
    """HEALTH-01: GET /health returns correct schema."""
    response = await client.get("/health")
    assert response.status_code == 200

    data = response.json()
    assert data["status"] == "ok"
    assert isinstance(data["uptime_seconds"], (int, float))
    assert isinstance(data["started_at"], str)
    assert "total_tasks" in data
    assert data["total_tasks"] == 0
    assert "tasks_by_status" in data
    tbs = data["tasks_by_status"]
    for key in ["open", "accepted", "submitted", "approved", "cancelled", "disputed", "ruled", "expired"]:
        assert key in tbs, f"Missing key: {key}"


@pytest.mark.unit
async def test_health_02_total_task_count(client, alice):
    """HEALTH-02: Total task count is exact after creating tasks."""
    from tests.unit.conftest import create_task

    await create_task(client, alice)
    await create_task(client, alice)

    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["total_tasks"] == 2


@pytest.mark.unit
async def test_health_03_uptime_monotonic(client):
    """HEALTH-03: Uptime increases between calls."""
    r1 = await client.get("/health")
    await asyncio.sleep(1.1)
    r2 = await client.get("/health")

    assert r2.json()["uptime_seconds"] > r1.json()["uptime_seconds"]


@pytest.mark.unit
async def test_health_04_tasks_by_status(client, alice):
    """HEALTH-04: tasks_by_status reflects actual state."""
    from tests.unit.conftest import create_task, make_jws

    task1 = await create_task(client, alice)
    task2 = await create_task(client, alice)

    # Cancel task2
    cancel_payload = {
        "action": "cancel_task",
        "task_id": task2["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, cancel_payload)
    resp = await client.post(f"/tasks/{task2['task_id']}/cancel", json={"token": token})
    assert resp.status_code == 200

    response = await client.get("/health")
    tbs = response.json()["tasks_by_status"]
    assert tbs["open"] == 1
    assert tbs["cancelled"] == 1
```

---

## File 9: `tests/unit/routers/test_tasks.py`

Create this file. Covers **Category 1: TC-01 to TC-28** and **Category 2: TQ-01 to TQ-13**.

```python
"""Unit tests for task creation and queries — Categories 1 and 2."""

from __future__ import annotations

import re
import uuid

import pytest

from tests.unit.conftest import (
    accept_bid,
    create_task,
    make_jws,
    make_keypair,
    make_tampered_jws,
    submit_bid,
)

# ---------------------------------------------------------------------------
# Category 1: Task Creation (TC-01 to TC-28)
# ---------------------------------------------------------------------------

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _valid_task_id() -> str:
    return f"t-{uuid.uuid4()}"


@pytest.mark.unit
async def test_tc01_create_valid_task(client, alice):
    """TC-01: Create a valid task with escrow."""
    task_id = _valid_task_id()
    task = await create_task(client, alice, task_id=task_id, reward=100)

    assert task["task_id"] == task_id
    assert task["poster_id"] == alice.agent_id
    assert task["status"] == "open"
    assert task["title"] == "Test task"
    assert task["reward"] == 100
    assert task["bid_count"] == 0
    assert task["escrow_pending"] is False
    assert UUID4_RE.match(task["escrow_id"].removeprefix("esc-"))
    assert task["worker_id"] is None
    assert task["accepted_bid_id"] is None
    assert task["accepted_at"] is None
    assert task["submitted_at"] is None
    assert task["approved_at"] is None
    assert task["cancelled_at"] is None
    assert task["disputed_at"] is None
    assert task["dispute_reason"] is None
    assert task["ruling_id"] is None
    assert task["ruled_at"] is None
    assert task["worker_pct"] is None
    assert task["ruling_summary"] is None
    assert task["expired_at"] is None
    assert task["created_at"] is not None
    assert task["bidding_deadline"] is not None
    assert task["execution_deadline"] is None
    assert task["review_deadline"] is None


@pytest.mark.unit
async def test_tc02_duplicate_task_id(client, alice):
    """TC-02: Duplicate task_id is rejected."""
    task_id = _valid_task_id()
    await create_task(client, alice, task_id=task_id)

    # Second creation with same ID
    task_payload = {
        "action": "create_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
        "title": "Duplicate",
        "spec": "Test",
        "reward": 50,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 600,
    }
    escrow_payload = {
        "action": "escrow_lock",
        "agent_id": alice.agent_id,
        "amount": 50,
        "task_id": task_id,
    }
    resp = await client.post("/tasks", json={
        "task_token": make_jws(alice, task_payload),
        "escrow_token": make_jws(alice, escrow_payload),
    })
    assert resp.status_code == 409
    assert resp.json()["error"] == "TASK_ALREADY_EXISTS"


@pytest.mark.unit
async def test_tc03_task_id_format_validation(client, alice):
    """TC-03: Invalid task_id formats are rejected."""
    invalid_ids = [
        "not-a-uuid",
        f"a-{uuid.uuid4()}",
        "t-invalid",
        "",
    ]
    for bad_id in invalid_ids:
        task_payload = {
            "action": "create_task",
            "task_id": bad_id,
            "poster_id": alice.agent_id,
            "title": "Test",
            "spec": "Test spec",
            "reward": 100,
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 3600,
            "review_deadline_seconds": 600,
        }
        escrow_payload = {
            "action": "escrow_lock",
            "agent_id": alice.agent_id,
            "amount": 100,
            "task_id": bad_id,
        }
        resp = await client.post("/tasks", json={
            "task_token": make_jws(alice, task_payload),
            "escrow_token": make_jws(alice, escrow_payload),
        })
        assert resp.status_code == 400, f"Expected 400 for task_id={bad_id!r}, got {resp.status_code}"
        assert resp.json()["error"] == "INVALID_TASK_ID", f"Expected INVALID_TASK_ID for {bad_id!r}"


@pytest.mark.unit
async def test_tc04_missing_task_token(client, alice):
    """TC-04: Missing task_token returns INVALID_JWS."""
    escrow_payload = {
        "action": "escrow_lock",
        "agent_id": alice.agent_id,
        "amount": 100,
        "task_id": _valid_task_id(),
    }
    resp = await client.post("/tasks", json={
        "escrow_token": make_jws(alice, escrow_payload),
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
async def test_tc05_missing_escrow_token(client, alice):
    """TC-05: Missing escrow_token returns INVALID_JWS."""
    task_payload = {
        "action": "create_task",
        "task_id": _valid_task_id(),
        "poster_id": alice.agent_id,
        "title": "Test",
        "spec": "Test",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 600,
    }
    resp = await client.post("/tasks", json={
        "task_token": make_jws(alice, task_payload),
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
async def test_tc06_both_tokens_missing(client):
    """TC-06: Both tokens missing returns INVALID_JWS."""
    resp = await client.post("/tasks", json={})
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
async def test_tc07_malformed_task_token(client, alice):
    """TC-07: Malformed task_token values return INVALID_JWS."""
    escrow_payload = {
        "action": "escrow_lock",
        "agent_id": alice.agent_id,
        "amount": 100,
        "task_id": _valid_task_id(),
    }
    escrow_token = make_jws(alice, escrow_payload)

    for bad_token in ["not-a-jws", "only.two-parts", 12345, None, ""]:
        resp = await client.post("/tasks", json={
            "task_token": bad_token,
            "escrow_token": escrow_token,
        })
        assert resp.status_code == 400, f"Expected 400 for {bad_token!r}"
        assert resp.json()["error"] == "INVALID_JWS", f"Expected INVALID_JWS for {bad_token!r}"


@pytest.mark.unit
async def test_tc08_wrong_action_in_task_token(client, alice):
    """TC-08: Wrong action in task_token returns INVALID_PAYLOAD."""
    task_id = _valid_task_id()
    task_payload = {
        "action": "submit_bid",
        "task_id": task_id,
        "poster_id": alice.agent_id,
        "title": "Test",
        "spec": "Test spec",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 600,
    }
    escrow_payload = {
        "action": "escrow_lock",
        "agent_id": alice.agent_id,
        "amount": 100,
        "task_id": task_id,
    }
    resp = await client.post("/tasks", json={
        "task_token": make_jws(alice, task_payload),
        "escrow_token": make_jws(alice, escrow_payload),
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_PAYLOAD"


@pytest.mark.unit
async def test_tc11_task_id_mismatch_between_tokens(client, alice):
    """TC-11: task_id mismatch between tokens returns TOKEN_MISMATCH."""
    tid_a = _valid_task_id()
    tid_b = _valid_task_id()
    task_payload = {
        "action": "create_task",
        "task_id": tid_a,
        "poster_id": alice.agent_id,
        "title": "Test",
        "spec": "Test spec",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 600,
    }
    escrow_payload = {
        "action": "escrow_lock",
        "agent_id": alice.agent_id,
        "amount": 100,
        "task_id": tid_b,
    }
    resp = await client.post("/tasks", json={
        "task_token": make_jws(alice, task_payload),
        "escrow_token": make_jws(alice, escrow_payload),
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "TOKEN_MISMATCH"


@pytest.mark.unit
async def test_tc12_reward_amount_mismatch(client, alice):
    """TC-12: reward/amount mismatch between tokens returns TOKEN_MISMATCH."""
    task_id = _valid_task_id()
    task_payload = {
        "action": "create_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
        "title": "Test",
        "spec": "Test",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 600,
    }
    escrow_payload = {
        "action": "escrow_lock",
        "agent_id": alice.agent_id,
        "amount": 50,
        "task_id": task_id,
    }
    resp = await client.post("/tasks", json={
        "task_token": make_jws(alice, task_payload),
        "escrow_token": make_jws(alice, escrow_payload),
    })
    assert resp.status_code == 400
    assert resp.json()["error"] == "TOKEN_MISMATCH"


@pytest.mark.unit
async def test_tc13_invalid_reward_values(client, alice):
    """TC-13: Invalid reward values return INVALID_REWARD."""
    for bad_reward in [0, -10, 1.5, "one hundred", None]:
        task_id = _valid_task_id()
        task_payload = {
            "action": "create_task",
            "task_id": task_id,
            "poster_id": alice.agent_id,
            "title": "Test",
            "spec": "Test",
            "reward": bad_reward,
            "bidding_deadline_seconds": 3600,
            "deadline_seconds": 3600,
            "review_deadline_seconds": 600,
        }
        escrow_payload = {
            "action": "escrow_lock",
            "agent_id": alice.agent_id,
            "amount": bad_reward,
            "task_id": task_id,
        }
        resp = await client.post("/tasks", json={
            "task_token": make_jws(alice, task_payload),
            "escrow_token": make_jws(alice, escrow_payload),
        })
        assert resp.status_code == 400, f"Expected 400 for reward={bad_reward!r}"
        assert resp.json()["error"] == "INVALID_REWARD", f"Expected INVALID_REWARD for {bad_reward!r}"


@pytest.mark.unit
async def test_tc20_tampered_task_token(client, alice):
    """TC-20: Tampered task_token returns FORBIDDEN."""
    task_id = _valid_task_id()
    task_payload = {
        "action": "create_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
        "title": "Test",
        "spec": "Test",
        "reward": 100,
        "bidding_deadline_seconds": 3600,
        "deadline_seconds": 3600,
        "review_deadline_seconds": 600,
    }
    escrow_payload = {
        "action": "escrow_lock",
        "agent_id": alice.agent_id,
        "amount": 100,
        "task_id": task_id,
    }
    tampered = make_tampered_jws(alice, task_payload)
    resp = await client.post("/tasks", json={
        "task_token": tampered,
        "escrow_token": make_jws(alice, escrow_payload),
    })
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_tc25_malformed_json(client):
    """TC-25: Malformed JSON body returns INVALID_JSON."""
    resp = await client.post(
        "/tasks",
        content=b"{broken",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_JSON"


@pytest.mark.unit
async def test_tc26_wrong_content_type(client):
    """TC-26: Wrong content type returns UNSUPPORTED_MEDIA_TYPE."""
    resp = await client.post(
        "/tasks",
        content=b'{"task_token": "test"}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    assert resp.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"


# ---------------------------------------------------------------------------
# Category 2: Task Queries (TQ-01 to TQ-13)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_tq01_get_task_by_id(client, alice):
    """TQ-01: Get a task by ID returns full task object."""
    task = await create_task(client, alice)
    resp = await client.get(f"/tasks/{task['task_id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == task["task_id"]
    assert data["poster_id"] == alice.agent_id
    assert data["status"] == "open"


@pytest.mark.unit
async def test_tq02_get_nonexistent_task(client):
    """TQ-02: Get non-existent task returns TASK_NOT_FOUND."""
    resp = await client.get("/tasks/t-00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    assert resp.json()["error"] == "TASK_NOT_FOUND"


@pytest.mark.unit
async def test_tq03_malformed_task_id_in_path(client):
    """TQ-03: Malformed task IDs return 404 with no internals leaked."""
    for bad_id in ["not-a-valid-id", "../../etc/passwd"]:
        resp = await client.get(f"/tasks/{bad_id}")
        assert resp.status_code == 404
        body = resp.text
        assert "Traceback" not in body
        assert "/" not in resp.json().get("message", "")  # no file paths


@pytest.mark.unit
async def test_tq05_list_tasks_empty(client):
    """TQ-05: List tasks on empty system returns empty list."""
    resp = await client.get("/tasks")
    assert resp.status_code == 200
    assert resp.json()["tasks"] == []


@pytest.mark.unit
async def test_tq06_list_tasks_returns_summary(client, alice):
    """TQ-06: List tasks returns summary fields, no detail-only fields."""
    await create_task(client, alice)
    await create_task(client, alice)

    resp = await client.get("/tasks")
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 2

    for t in tasks:
        assert "task_id" in t
        assert "title" in t
        assert "status" in t
        assert "bid_count" in t
        # Detail-only fields should NOT be in summary
        assert "spec" not in t
        assert "dispute_reason" not in t
        assert "ruling_summary" not in t


@pytest.mark.unit
async def test_tq07_filter_by_status(client, alice):
    """TQ-07: Filter tasks by status."""
    task1 = await create_task(client, alice)
    task2 = await create_task(client, alice)

    # Cancel task2
    cancel_payload = {
        "action": "cancel_task",
        "task_id": task2["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, cancel_payload)
    await client.post(f"/tasks/{task2['task_id']}/cancel", json={"token": token})

    resp = await client.get("/tasks?status=open")
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == task1["task_id"]


@pytest.mark.unit
async def test_tq12_no_auth_required(client, alice):
    """TQ-12: GET /tasks and GET /tasks/{id} require no authentication."""
    task = await create_task(client, alice)
    resp1 = await client.get("/tasks")
    assert resp1.status_code == 200
    resp2 = await client.get(f"/tasks/{task['task_id']}")
    assert resp2.status_code == 200


@pytest.mark.unit
async def test_tq13_idempotent_read(client, alice):
    """TQ-13: GET /tasks/{id} twice returns identical JSON."""
    task = await create_task(client, alice)
    r1 = await client.get(f"/tasks/{task['task_id']}")
    r2 = await client.get(f"/tasks/{task['task_id']}")
    assert r1.json() == r2.json()
```

**Remaining tests to implement following the same pattern:**
- TC-09 (missing fields in payload — loop over each field, assert 400 INVALID_PAYLOAD)
- TC-10 (signer mismatch — alice signs with bob's poster_id, assert 403 FORBIDDEN)
- TC-14a/b/c (invalid deadline values — loop per field, assert 400 INVALID_DEADLINE)
- TC-15 to TC-18 (title/spec length validation)
- TC-19 (insufficient funds — set `central_bank_mock_state.force_insufficient_funds = True`, assert 402)
- TC-21 (unregistered agent — set `identity_mock_state.force_invalid = True`, assert 403)
- TC-22 (identity unavailable — set `identity_mock_state.force_unavailable = True`, assert 502)
- TC-23 (central bank unavailable — set `central_bank_mock_state.force_unavailable = True`, assert 502)
- TC-24 (mass assignment — include extra fields, verify ignored)
- TC-27 (oversized body — send body exceeding max_body_size, assert 413)
- TC-28 (escrow rollback — pre-insert task_id directly in DB, verify release call)
- TQ-04, TQ-08, TQ-09, TQ-10, TQ-11 (query filters — follow TQ-07 pattern)

---

## File 10: `tests/unit/routers/test_cancel.py`

Create this file. Covers **Category 3: CAN-01 to CAN-09**.

```python
"""Unit tests for task cancellation — Category 3."""

from __future__ import annotations

import pytest

from tests.unit.conftest import create_task, make_jws, submit_bid, accept_bid


@pytest.mark.unit
async def test_can01_poster_cancels_open_task(client, alice, central_bank_mock_state):
    """CAN-01: Poster cancels an OPEN task."""
    task = await create_task(client, alice)
    payload = {
        "action": "cancel_task",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/cancel", json={"token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["cancelled_at"] is not None
    # Verify escrow was released
    assert len(central_bank_mock_state.release_calls) >= 1


@pytest.mark.unit
async def test_can02_non_poster_cannot_cancel(client, alice, bob):
    """CAN-02: Non-poster cannot cancel."""
    task = await create_task(client, alice)
    payload = {
        "action": "cancel_task",
        "task_id": task["task_id"],
        "poster_id": bob.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/cancel", json={"token": token})
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_can03_impersonation_cancel(client, alice, bob):
    """CAN-03: Bob signs cancel with poster_id=alice."""
    task = await create_task(client, alice)
    payload = {
        "action": "cancel_task",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/cancel", json={"token": token})
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_can04_cannot_cancel_non_open(client, alice, bob):
    """CAN-04: Cannot cancel task that is not OPEN."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    payload = {
        "action": "cancel_task",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/cancel", json={"token": token})
    assert resp.status_code == 409
    assert resp.json()["error"] == "INVALID_STATUS"


@pytest.mark.unit
async def test_can05_cancel_nonexistent(client, alice):
    """CAN-05: Cancel non-existent task returns TASK_NOT_FOUND."""
    tid = "t-00000000-0000-0000-0000-000000000000"
    payload = {
        "action": "cancel_task",
        "task_id": tid,
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{tid}/cancel", json={"token": token})
    assert resp.status_code == 404
    assert resp.json()["error"] == "TASK_NOT_FOUND"
```

**Remaining:** CAN-06 (wrong action), CAN-07 (bank unavailable on release), CAN-08 (malformed token), CAN-09 (task_id path mismatch). Follow the same patterns shown above.

---

## File 11: `tests/unit/routers/test_bids.py`

Create this file. Covers **Category 4: BID-01 to BID-15** and **Category 5: BL-01 to BL-08**.

```python
"""Unit tests for bidding and bid listing — Categories 4 and 5."""

from __future__ import annotations

import asyncio
import re

import pytest

from tests.unit.conftest import (
    accept_bid,
    create_task,
    make_jws,
    submit_bid,
)

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Category 4: Bidding (BID-01 to BID-15)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_bid01_valid_bid(client, alice, bob):
    """BID-01: Submit a valid bid returns 201 with correct fields."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    assert bid["task_id"] == task["task_id"]
    assert bid["bidder_id"] == bob.agent_id
    assert UUID4_RE.match(bid["bid_id"].removeprefix("bid-"))
    assert bid["submitted_at"] is not None
    assert "proposal" in bid


@pytest.mark.unit
async def test_bid02_multiple_bids(client, alice, bob, carol):
    """BID-02: Multiple agents can bid on same task."""
    task = await create_task(client, alice)
    bid_bob = await submit_bid(client, bob, task["task_id"])
    bid_carol = await submit_bid(client, carol, task["task_id"])
    assert bid_bob["bid_id"] != bid_carol["bid_id"]

    # Check bid_count
    resp = await client.get(f"/tasks/{task['task_id']}")
    assert resp.json()["bid_count"] == 2


@pytest.mark.unit
async def test_bid03_self_bid_rejected(client, alice):
    """BID-03: Poster cannot bid on own task."""
    task = await create_task(client, alice)
    payload = {
        "action": "submit_bid",
        "task_id": task["task_id"],
        "bidder_id": alice.agent_id,
        "proposal": "Self bid",
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/bids", json={"token": token})
    assert resp.status_code == 400
    assert resp.json()["error"] == "SELF_BID"


@pytest.mark.unit
async def test_bid04_duplicate_bid(client, alice, bob):
    """BID-04: Duplicate bid is rejected."""
    task = await create_task(client, alice)
    await submit_bid(client, bob, task["task_id"])

    payload = {
        "action": "submit_bid",
        "task_id": task["task_id"],
        "bidder_id": bob.agent_id,
        "proposal": "Second bid",
    }
    token = make_jws(bob, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/bids", json={"token": token})
    assert resp.status_code == 409
    assert resp.json()["error"] == "BID_ALREADY_EXISTS"


@pytest.mark.unit
async def test_bid05_bid_on_non_open(client, alice, bob, carol):
    """BID-05: Bid on non-OPEN task is rejected."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    payload = {
        "action": "submit_bid",
        "task_id": task["task_id"],
        "bidder_id": carol.agent_id,
        "proposal": "Late bid",
    }
    token = make_jws(carol, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/bids", json={"token": token})
    assert resp.status_code == 409
    assert resp.json()["error"] == "INVALID_STATUS"


@pytest.mark.unit
async def test_bid06_bid_nonexistent_task(client, bob):
    """BID-06: Bid on non-existent task returns TASK_NOT_FOUND."""
    tid = "t-00000000-0000-0000-0000-000000000000"
    payload = {
        "action": "submit_bid",
        "task_id": tid,
        "bidder_id": bob.agent_id,
        "proposal": "Test",
    }
    token = make_jws(bob, payload)
    resp = await client.post(f"/tasks/{tid}/bids", json={"token": token})
    assert resp.status_code == 404
    assert resp.json()["error"] == "TASK_NOT_FOUND"


@pytest.mark.unit
async def test_bid14_concurrent_duplicate_safe(client, alice, bob):
    """BID-14: Concurrent duplicate bids are safe — one 201, one 409."""
    task = await create_task(client, alice)
    payload = {
        "action": "submit_bid",
        "task_id": task["task_id"],
        "bidder_id": bob.agent_id,
        "proposal": "Concurrent bid",
    }
    token = make_jws(bob, payload)

    async def do_bid():
        return await client.post(
            f"/tasks/{task['task_id']}/bids", json={"token": token}
        )

    r1, r2 = await asyncio.gather(do_bid(), do_bid())
    codes = sorted([r1.status_code, r2.status_code])
    assert codes == [201, 409]


# ---------------------------------------------------------------------------
# Category 5: Bid Listing (BL-01 to BL-08)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_bl01_poster_lists_sealed_bids(client, alice, bob):
    """BL-01: Poster can list bids during OPEN phase."""
    task = await create_task(client, alice)
    await submit_bid(client, bob, task["task_id"])

    payload = {
        "action": "list_bids",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.get(
        f"/tasks/{task['task_id']}/bids",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["task_id"] == task["task_id"]
    assert len(data["bids"]) == 1
    assert data["bids"][0]["bidder_id"] == bob.agent_id


@pytest.mark.unit
async def test_bl02_non_poster_cannot_list_during_open(client, alice, bob):
    """BL-02: Non-poster cannot list bids during OPEN phase."""
    task = await create_task(client, alice)
    await submit_bid(client, bob, task["task_id"])

    payload = {
        "action": "list_bids",
        "task_id": task["task_id"],
        "poster_id": bob.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.get(
        f"/tasks/{task['task_id']}/bids",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_bl03_no_auth_during_open(client, alice, bob):
    """BL-03: No auth header during OPEN returns INVALID_JWS."""
    task = await create_task(client, alice)
    await submit_bid(client, bob, task["task_id"])

    resp = await client.get(f"/tasks/{task['task_id']}/bids")
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
async def test_bl04_bids_public_after_acceptance(client, alice, bob):
    """BL-04: Bids are public after bid acceptance."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    resp = await client.get(f"/tasks/{task['task_id']}/bids")
    assert resp.status_code == 200
    assert len(resp.json()["bids"]) == 1
```

**Remaining:** BID-07 to BID-13, BID-15, BL-05 to BL-08. Follow the same patterns.

---

## File 12: `tests/unit/routers/test_bid_accept.py`

Create this file. Covers **Category 6: BA-01 to BA-10**.

```python
"""Unit tests for bid acceptance — Category 6."""

from __future__ import annotations

import pytest

from tests.unit.conftest import accept_bid, create_task, make_jws, submit_bid


@pytest.mark.unit
async def test_ba01_poster_accepts_bid(client, alice, bob):
    """BA-01: Poster accepts a bid."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    result = await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    assert result["status"] == "accepted"
    assert result["worker_id"] == bob.agent_id
    assert result["accepted_bid_id"] == bid["bid_id"]
    assert result["accepted_at"] is not None
    assert result["execution_deadline"] is not None


@pytest.mark.unit
async def test_ba02_non_poster_cannot_accept(client, alice, bob, carol):
    """BA-02: Non-poster cannot accept a bid."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])

    payload = {
        "action": "accept_bid",
        "task_id": task["task_id"],
        "bid_id": bid["bid_id"],
        "poster_id": carol.agent_id,
    }
    token = make_jws(carol, payload)
    resp = await client.post(
        f"/tasks/{task['task_id']}/bids/{bid['bid_id']}/accept",
        json={"token": token},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_ba03_accept_nonexistent_bid(client, alice):
    """BA-03: Accept non-existent bid returns BID_NOT_FOUND."""
    task = await create_task(client, alice)
    fake_bid_id = "bid-00000000-0000-0000-0000-000000000000"
    payload = {
        "action": "accept_bid",
        "task_id": task["task_id"],
        "bid_id": fake_bid_id,
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.post(
        f"/tasks/{task['task_id']}/bids/{fake_bid_id}/accept",
        json={"token": token},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "BID_NOT_FOUND"


@pytest.mark.unit
async def test_ba04_cannot_accept_on_non_open(client, alice, bob, carol):
    """BA-04: Cannot accept bid on non-OPEN task."""
    task = await create_task(client, alice)
    bid_bob = await submit_bid(client, bob, task["task_id"])
    bid_carol = await submit_bid(client, carol, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid_bob["bid_id"])

    payload = {
        "action": "accept_bid",
        "task_id": task["task_id"],
        "bid_id": bid_carol["bid_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.post(
        f"/tasks/{task['task_id']}/bids/{bid_carol['bid_id']}/accept",
        json={"token": token},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "INVALID_STATUS"
```

**Remaining:** BA-05 to BA-10. Follow the same patterns.

---

## File 13: `tests/unit/routers/test_assets.py`

Create this file. Covers **Category 7: AU-01 to AU-11** and **Category 8: AR-01 to AR-06**.

```python
"""Unit tests for asset upload and retrieval — Categories 7 and 8."""

from __future__ import annotations

import re

import pytest

from tests.unit.conftest import (
    accept_bid,
    create_task,
    make_jws,
    submit_bid,
    upload_asset,
)

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


# ---------------------------------------------------------------------------
# Category 7: Asset Upload (AU-01 to AU-11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_au01_worker_uploads_file(client, alice, bob):
    """AU-01: Worker uploads a file successfully."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    asset = await upload_asset(
        client, bob, task["task_id"],
        filename="login-page.zip",
        content=b"zipdata",
        content_type="application/zip",
    )
    assert asset["task_id"] == task["task_id"]
    assert asset["uploader_id"] == bob.agent_id
    assert asset["filename"] == "login-page.zip"
    assert asset["content_type"] == "application/zip"
    assert asset["size_bytes"] == len(b"zipdata")
    assert UUID4_RE.match(asset["asset_id"].removeprefix("asset-"))
    assert asset["uploaded_at"] is not None


@pytest.mark.unit
async def test_au02_non_worker_cannot_upload(client, alice, bob, carol):
    """AU-02: Non-worker cannot upload."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    payload = {
        "action": "upload_asset",
        "task_id": task["task_id"],
        "worker_id": carol.agent_id,
    }
    token = make_jws(carol, payload)
    resp = await client.post(
        f"/tasks/{task['task_id']}/assets",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.zip", b"data", "application/zip")},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_au04_cannot_upload_to_non_accepted(client, alice, bob):
    """AU-04: Cannot upload to non-ACCEPTED task."""
    task = await create_task(client, alice)

    payload = {
        "action": "upload_asset",
        "task_id": task["task_id"],
        "worker_id": bob.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.post(
        f"/tasks/{task['task_id']}/assets",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("test.zip", b"data", "application/zip")},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "INVALID_STATUS"


@pytest.mark.unit
async def test_au07_no_file_part(client, alice, bob):
    """AU-07: No file part in multipart returns NO_FILE."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    payload = {
        "action": "upload_asset",
        "task_id": task["task_id"],
        "worker_id": bob.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.post(
        f"/tasks/{task['task_id']}/assets",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "multipart/form-data; boundary=----boundary",
        },
        content=b"------boundary--\r\n",
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "NO_FILE"


# ---------------------------------------------------------------------------
# Category 8: Asset Retrieval (AR-01 to AR-06)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_ar01_list_assets(client, alice, bob):
    """AR-01: List assets for a task."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])
    await upload_asset(client, bob, task["task_id"], filename="file1.zip")
    await upload_asset(client, bob, task["task_id"], filename="file2.zip")

    resp = await client.get(f"/tasks/{task['task_id']}/assets")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["assets"]) == 2


@pytest.mark.unit
async def test_ar03_download_asset(client, alice, bob):
    """AR-03: Download an asset returns exact file content."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    file_content = b"test content for download"
    asset = await upload_asset(
        client, bob, task["task_id"],
        filename="login-page.zip",
        content=file_content,
        content_type="application/zip",
    )

    resp = await client.get(f"/tasks/{task['task_id']}/assets/{asset['asset_id']}")
    assert resp.status_code == 200
    assert resp.content == file_content
    assert "login-page.zip" in resp.headers.get("content-disposition", "")


@pytest.mark.unit
async def test_ar04_download_nonexistent_asset(client, alice):
    """AR-04: Download non-existent asset returns ASSET_NOT_FOUND."""
    task = await create_task(client, alice)
    resp = await client.get(
        f"/tasks/{task['task_id']}/assets/asset-00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "ASSET_NOT_FOUND"
```

**Remaining:** AU-03, AU-05, AU-06, AU-08 to AU-11, AR-02, AR-05, AR-06. Follow the same patterns.

---

## File 14: `tests/unit/routers/test_submit.py`

Create this file. Covers **Category 9: SUB-01 to SUB-09**.

```python
"""Unit tests for deliverable submission — Category 9."""

from __future__ import annotations

import pytest

from tests.unit.conftest import (
    accept_bid,
    create_task,
    full_lifecycle_to_submitted,
    make_jws,
    submit_bid,
    submit_deliverable,
    upload_asset,
)


@pytest.mark.unit
async def test_sub01_worker_submits(client, alice, bob):
    """SUB-01: Worker submits deliverable."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])
    await upload_asset(client, bob, task["task_id"])

    result = await submit_deliverable(client, bob, task["task_id"])
    assert result["status"] == "submitted"
    assert result["submitted_at"] is not None
    assert result["review_deadline"] is not None


@pytest.mark.unit
async def test_sub02_non_worker_cannot_submit(client, alice, bob, carol):
    """SUB-02: Non-worker cannot submit."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])
    await upload_asset(client, bob, task["task_id"])

    payload = {
        "action": "submit_deliverable",
        "task_id": task["task_id"],
        "worker_id": carol.agent_id,
    }
    token = make_jws(carol, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/submit", json={"token": token})
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_sub04_cannot_submit_without_assets(client, alice, bob):
    """SUB-04: Cannot submit without assets."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    payload = {
        "action": "submit_deliverable",
        "task_id": task["task_id"],
        "worker_id": bob.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/submit", json={"token": token})
    assert resp.status_code == 400
    assert resp.json()["error"] == "NO_ASSETS"


@pytest.mark.unit
async def test_sub06_double_submit(client, alice, bob):
    """SUB-06: Cannot submit from SUBMITTED status."""
    await full_lifecycle_to_submitted(client, alice, bob)
    task_resp = await client.get("/tasks")
    task_id = task_resp.json()["tasks"][0]["task_id"]

    payload = {
        "action": "submit_deliverable",
        "task_id": task_id,
        "worker_id": bob.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.post(f"/tasks/{task_id}/submit", json={"token": token})
    assert resp.status_code == 409
    assert resp.json()["error"] == "INVALID_STATUS"
```

**Remaining:** SUB-03, SUB-05, SUB-07, SUB-08, SUB-09. Follow the same patterns.

---

## File 15: `tests/unit/routers/test_approve.py`

Create this file. Covers **Category 10: APP-01 to APP-09**.

```python
"""Unit tests for approval — Category 10."""

from __future__ import annotations

import pytest

from tests.unit.conftest import (
    full_lifecycle_to_submitted,
    make_jws,
)


@pytest.mark.unit
async def test_app01_poster_approves(client, alice, bob, central_bank_mock_state):
    """APP-01: Poster approves deliverable."""
    submitted = await full_lifecycle_to_submitted(client, alice, bob)
    task_id = submitted["task_id"]

    payload = {
        "action": "approve_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task_id}/approve", json={"token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "approved"
    assert data["approved_at"] is not None
    # Verify escrow release was called
    assert len(central_bank_mock_state.release_calls) >= 1


@pytest.mark.unit
async def test_app02_non_poster_cannot_approve(client, alice, bob, carol):
    """APP-02: Non-poster cannot approve."""
    submitted = await full_lifecycle_to_submitted(client, alice, bob)
    task_id = submitted["task_id"]

    payload = {
        "action": "approve_task",
        "task_id": task_id,
        "poster_id": carol.agent_id,
    }
    token = make_jws(carol, payload)
    resp = await client.post(f"/tasks/{task_id}/approve", json={"token": token})
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_app04_cannot_approve_non_submitted(client, alice):
    """APP-04: Cannot approve non-SUBMITTED task."""
    from tests.unit.conftest import create_task

    task = await create_task(client, alice)
    payload = {
        "action": "approve_task",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/approve", json={"token": token})
    assert resp.status_code == 409
    assert resp.json()["error"] == "INVALID_STATUS"
```

**Remaining:** APP-03, APP-05 to APP-09. Follow the same patterns.

---

## File 16: `tests/unit/routers/test_dispute.py`

Create this file. Covers **Category 11: DIS-01 to DIS-10**.

```python
"""Unit tests for dispute — Category 11."""

from __future__ import annotations

import pytest

from tests.unit.conftest import full_lifecycle_to_submitted, make_jws


@pytest.mark.unit
async def test_dis01_poster_disputes(client, alice, bob):
    """DIS-01: Poster disputes deliverable."""
    submitted = await full_lifecycle_to_submitted(client, alice, bob)
    task_id = submitted["task_id"]

    payload = {
        "action": "dispute_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
        "reason": "The login page does not validate email format.",
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task_id}/dispute", json={"token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "disputed"
    assert data["disputed_at"] is not None
    assert data["dispute_reason"] == "The login page does not validate email format."


@pytest.mark.unit
async def test_dis02_non_poster_cannot_dispute(client, alice, bob, carol):
    """DIS-02: Non-poster cannot dispute."""
    submitted = await full_lifecycle_to_submitted(client, alice, bob)
    task_id = submitted["task_id"]

    payload = {
        "action": "dispute_task",
        "task_id": task_id,
        "poster_id": carol.agent_id,
        "reason": "Unauthorized dispute",
    }
    token = make_jws(carol, payload)
    resp = await client.post(f"/tasks/{task_id}/dispute", json={"token": token})
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_dis05_empty_reason(client, alice, bob):
    """DIS-05: Empty dispute reason returns INVALID_REASON."""
    submitted = await full_lifecycle_to_submitted(client, alice, bob)
    task_id = submitted["task_id"]

    payload = {
        "action": "dispute_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
        "reason": "",
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task_id}/dispute", json={"token": token})
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_REASON"


@pytest.mark.unit
async def test_dis06_reason_exceeds_max(client, alice, bob):
    """DIS-06: Dispute reason exceeding 10000 chars returns INVALID_REASON."""
    submitted = await full_lifecycle_to_submitted(client, alice, bob)
    task_id = submitted["task_id"]

    payload = {
        "action": "dispute_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
        "reason": "x" * 10001,
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task_id}/dispute", json={"token": token})
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_REASON"
```

**Remaining:** DIS-03, DIS-04, DIS-07 to DIS-10. Follow the same patterns.

---

## File 17: `tests/unit/routers/test_ruling.py`

Create this file. Covers **Category 12: RUL-01 to RUL-13**.

```python
"""Unit tests for rulings — Category 12."""

from __future__ import annotations

import uuid

import pytest

from tests.unit.conftest import full_lifecycle_to_disputed, make_jws


@pytest.mark.unit
async def test_rul01_platform_records_ruling(client, alice, bob, platform_agent):
    """RUL-01: Platform records a ruling."""
    disputed = await full_lifecycle_to_disputed(client, alice, bob)
    task_id = disputed["task_id"]
    ruling_id = f"rul-{uuid.uuid4()}"

    payload = {
        "action": "record_ruling",
        "task_id": task_id,
        "ruling_id": ruling_id,
        "worker_pct": 40,
        "ruling_summary": "Worker delivered but omitted email validation.",
    }
    token = make_jws(platform_agent, payload)
    resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ruled"
    assert data["ruled_at"] is not None
    assert data["ruling_id"] == ruling_id
    assert data["worker_pct"] == 40
    assert data["ruling_summary"] == "Worker delivered but omitted email validation."


@pytest.mark.unit
async def test_rul02_non_platform_cannot_rule(client, alice, bob):
    """RUL-02: Non-platform agent cannot record ruling."""
    disputed = await full_lifecycle_to_disputed(client, alice, bob)
    task_id = disputed["task_id"]

    payload = {
        "action": "record_ruling",
        "task_id": task_id,
        "ruling_id": f"rul-{uuid.uuid4()}",
        "worker_pct": 50,
        "ruling_summary": "Test",
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_rul04_worker_pct_zero(client, alice, bob, platform_agent):
    """RUL-04: worker_pct=0 is valid (full poster win)."""
    disputed = await full_lifecycle_to_disputed(client, alice, bob)
    task_id = disputed["task_id"]

    payload = {
        "action": "record_ruling",
        "task_id": task_id,
        "ruling_id": f"rul-{uuid.uuid4()}",
        "worker_pct": 0,
        "ruling_summary": "Full poster win.",
    }
    token = make_jws(platform_agent, payload)
    resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["worker_pct"] == 0


@pytest.mark.unit
async def test_rul06_invalid_worker_pct(client, alice, bob, platform_agent):
    """RUL-06: Invalid worker_pct values return INVALID_WORKER_PCT."""
    disputed = await full_lifecycle_to_disputed(client, alice, bob)
    task_id = disputed["task_id"]

    for bad_pct in [-1, 101, 50.5, "fifty", None]:
        payload = {
            "action": "record_ruling",
            "task_id": task_id,
            "ruling_id": f"rul-{uuid.uuid4()}",
            "worker_pct": bad_pct,
            "ruling_summary": "Test",
        }
        token = make_jws(platform_agent, payload)
        resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
        assert resp.status_code == 400, f"Expected 400 for worker_pct={bad_pct!r}"
        assert resp.json()["error"] == "INVALID_WORKER_PCT", f"For {bad_pct!r}"
```

**Remaining:** RUL-03, RUL-05, RUL-07 to RUL-13. Follow the same patterns.

---

## File 18: `tests/unit/routers/test_lifecycle.py`

Create this file. Covers **Category 13: LIFE-01 to LIFE-12**.

```python
"""Unit tests for lifecycle and deadlines — Category 13."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tests.unit.conftest import (
    accept_bid,
    create_task,
    full_lifecycle_to_disputed,
    full_lifecycle_to_submitted,
    make_jws,
    submit_bid,
    submit_deliverable,
    upload_asset,
)


@pytest.mark.unit
async def test_life01_full_happy_path(client, alice, bob, central_bank_mock_state):
    """LIFE-01: Full happy path through APPROVED."""
    task = await create_task(client, alice)
    assert task["status"] == "open"

    bid = await submit_bid(client, bob, task["task_id"])

    accepted = await accept_bid(client, alice, task["task_id"], bid["bid_id"])
    assert accepted["status"] == "accepted"

    await upload_asset(client, bob, task["task_id"])

    submitted = await submit_deliverable(client, bob, task["task_id"])
    assert submitted["status"] == "submitted"

    payload = {
        "action": "approve_task",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(alice, payload)
    resp = await client.post(f"/tasks/{task['task_id']}/approve", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert len(central_bank_mock_state.release_calls) >= 1


@pytest.mark.unit
async def test_life02_dispute_flow(client, alice, bob, platform_agent):
    """LIFE-02: Full dispute flow through RULED."""
    disputed = await full_lifecycle_to_disputed(client, alice, bob)
    task_id = disputed["task_id"]

    payload = {
        "action": "record_ruling",
        "task_id": task_id,
        "ruling_id": f"rul-{uuid.uuid4()}",
        "worker_pct": 60,
        "ruling_summary": "Partial delivery.",
    }
    token = make_jws(platform_agent, payload)
    resp = await client.post(f"/tasks/{task_id}/ruling", json={"token": token})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ruled"
    assert data["worker_pct"] == 60
    assert data["dispute_reason"] is not None
    assert data["ruling_summary"] is not None


@pytest.mark.unit
async def test_life03_bidding_deadline_expires(client, alice, central_bank_mock_state):
    """LIFE-03: Bidding deadline auto-expires via lazy evaluation."""
    task = await create_task(client, alice, bidding_deadline_seconds=1)
    await asyncio.sleep(2)

    resp = await client.get(f"/tasks/{task['task_id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "expired"
    assert data["expired_at"] is not None
    # Escrow should be released back to poster
    assert len(central_bank_mock_state.release_calls) >= 1


@pytest.mark.unit
async def test_life04_execution_deadline_expires(client, alice, bob, central_bank_mock_state):
    """LIFE-04: Execution deadline auto-expires."""
    task = await create_task(client, alice, deadline_seconds=1)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])
    await asyncio.sleep(2)

    resp = await client.get(f"/tasks/{task['task_id']}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "expired"
    assert len(central_bank_mock_state.release_calls) >= 1


@pytest.mark.unit
async def test_life05_review_deadline_auto_approves(client, alice, bob, central_bank_mock_state):
    """LIFE-05: Review deadline auto-approves."""
    task = await create_task(client, alice, review_deadline_seconds=1)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])
    await upload_asset(client, bob, task["task_id"])
    await submit_deliverable(client, bob, task["task_id"])
    await asyncio.sleep(2)

    resp = await client.get(f"/tasks/{task['task_id']}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    assert resp.json()["approved_at"] is not None
    assert len(central_bank_mock_state.release_calls) >= 1


@pytest.mark.unit
async def test_life09_terminal_states_block_mutations(client, alice, bob):
    """LIFE-09: Terminal states (cancelled) block all mutations."""
    task = await create_task(client, alice)
    cancel_payload = {
        "action": "cancel_task",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    }
    await client.post(
        f"/tasks/{task['task_id']}/cancel",
        json={"token": make_jws(alice, cancel_payload)},
    )

    # Try to bid
    bid_payload = {
        "action": "submit_bid",
        "task_id": task["task_id"],
        "bidder_id": bob.agent_id,
        "proposal": "Late bid",
    }
    resp = await client.post(
        f"/tasks/{task['task_id']}/bids",
        json={"token": make_jws(bob, bid_payload)},
    )
    assert resp.status_code == 409
    assert resp.json()["error"] == "INVALID_STATUS"
```

**Remaining:** LIFE-06, LIFE-07, LIFE-08, LIFE-10, LIFE-11, LIFE-12. Follow the same patterns.

---

## File 19: `tests/unit/routers/test_http_methods.py`

Create this file. Covers **Category 15: HTTP-01**.

```python
"""Unit tests for HTTP method enforcement — Category 15."""

from __future__ import annotations

import pytest

from tests.unit.conftest import create_task


@pytest.mark.unit
async def test_http01_wrong_methods(client, alice):
    """HTTP-01: Unsupported methods return 405 METHOD_NOT_ALLOWED."""
    task = await create_task(client, alice)
    tid = task["task_id"]

    cases = [
        ("PUT", "/tasks"),
        ("DELETE", "/tasks"),
        ("PATCH", "/tasks"),
        ("PUT", f"/tasks/{tid}"),
        ("DELETE", f"/tasks/{tid}"),
        ("PATCH", f"/tasks/{tid}"),
        ("POST", f"/tasks/{tid}"),
        ("GET", f"/tasks/{tid}/cancel"),
        ("PUT", f"/tasks/{tid}/cancel"),
        ("DELETE", f"/tasks/{tid}/cancel"),
        ("PUT", f"/tasks/{tid}/bids"),
        ("DELETE", f"/tasks/{tid}/bids"),
        ("PATCH", f"/tasks/{tid}/bids"),
        ("POST", "/health"),
        ("PUT", "/health"),
        ("DELETE", "/health"),
    ]

    for method, path in cases:
        resp = await client.request(method, path)
        assert resp.status_code == 405, f"Expected 405 for {method} {path}, got {resp.status_code}"
        assert resp.json()["error"] == "METHOD_NOT_ALLOWED", f"For {method} {path}"
```

---

## File 20: `tests/unit/routers/test_error_precedence.py`

Create this file. Covers **Category 16: PREC-01 to PREC-10**.

```python
"""Unit tests for error precedence — Category 16."""

from __future__ import annotations

import pytest

from tests.unit.conftest import (
    create_task,
    full_lifecycle_to_submitted,
    make_jws,
    make_tampered_jws,
)


@pytest.mark.unit
async def test_prec01_content_type_before_token(client):
    """PREC-01: Content-Type checked before token validation."""
    resp = await client.post(
        "/tasks",
        content=b'{"task_token": "invalid"}',
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 415
    assert resp.json()["error"] == "UNSUPPORTED_MEDIA_TYPE"


@pytest.mark.unit
async def test_prec03_json_before_token(client):
    """PREC-03: JSON parsing checked before token validation."""
    resp = await client.post(
        "/tasks",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_JSON"


@pytest.mark.unit
async def test_prec04_token_before_payload(client, alice):
    """PREC-04: Token validation checked before payload validation."""
    task = await create_task(client, alice)
    resp = await client.post(
        f"/tasks/{task['task_id']}/cancel",
        json={"token": 12345},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_JWS"


@pytest.mark.unit
async def test_prec06_signature_before_payload(client, alice):
    """PREC-06: Signature validity checked before payload content."""
    task = await create_task(client, alice)
    tampered = make_tampered_jws(alice, {
        "action": "wrong_action",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    })
    resp = await client.post(
        f"/tasks/{task['task_id']}/cancel",
        json={"token": tampered},
    )
    assert resp.status_code == 403
    assert resp.json()["error"] == "FORBIDDEN"


@pytest.mark.unit
async def test_prec07_action_before_signer(client, alice, bob):
    """PREC-07: Payload action checked before signer matching."""
    task = await create_task(client, alice)
    payload = {
        "action": "submit_bid",
        "task_id": task["task_id"],
        "poster_id": alice.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.post(
        f"/tasks/{task['task_id']}/cancel",
        json={"token": token},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_PAYLOAD"


@pytest.mark.unit
async def test_prec08_task_lookup_before_role_match(client, bob):
    """PREC-08: Task lookup checked before role-dependent signer matching."""
    tid = "t-00000000-0000-0000-0000-999999999999"
    payload = {
        "action": "cancel_task",
        "task_id": tid,
        "poster_id": bob.agent_id,
    }
    token = make_jws(bob, payload)
    resp = await client.post(f"/tasks/{tid}/cancel", json={"token": token})
    assert resp.status_code == 404
    assert resp.json()["error"] == "TASK_NOT_FOUND"


@pytest.mark.unit
async def test_prec09_status_before_domain(client, alice, bob):
    """PREC-09: Task status checked before domain validation."""
    submitted = await full_lifecycle_to_submitted(client, alice, bob)
    task_id = submitted["task_id"]

    # Approve the task first to reach terminal state
    approve_payload = {
        "action": "approve_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
    }
    await client.post(
        f"/tasks/{task_id}/approve",
        json={"token": make_jws(alice, approve_payload)},
    )

    # Now dispute with empty reason (wrong status AND invalid reason)
    dispute_payload = {
        "action": "dispute_task",
        "task_id": task_id,
        "poster_id": alice.agent_id,
        "reason": "",
    }
    token = make_jws(alice, dispute_payload)
    resp = await client.post(f"/tasks/{task_id}/dispute", json={"token": token})
    assert resp.status_code == 409
    assert resp.json()["error"] == "INVALID_STATUS"
```

**Remaining:** PREC-02 (body size check), PREC-05 (identity unavailable), PREC-10 (token mismatch before bank).

---

## File 21: `tests/unit/routers/test_security.py`

Create this file. Covers **Category 17: SEC-01 to SEC-09**.

```python
"""Unit tests for cross-cutting security — Category 17."""

from __future__ import annotations

import re

import pytest

from tests.unit.conftest import (
    accept_bid,
    create_task,
    make_jws,
    submit_bid,
    upload_asset,
)

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


@pytest.mark.unit
async def test_sec01_error_envelope_consistency(client):
    """SEC-01: All error responses have error, message, details."""
    # Trigger INVALID_JSON
    resp = await client.post(
        "/tasks",
        content=b"{broken",
        headers={"Content-Type": "application/json"},
    )
    data = resp.json()
    assert isinstance(data["error"], str)
    assert isinstance(data["message"], str)
    assert isinstance(data["details"], dict)


@pytest.mark.unit
async def test_sec02_no_internal_leakage(client, alice):
    """SEC-02: No stack traces, SQL, or file paths in error messages."""
    error_triggers = [
        ("/tasks", "POST", b"{broken", {"Content-Type": "application/json"}),
    ]
    for path, method, body, headers in error_triggers:
        resp = await client.request(method, path, content=body, headers=headers)
        text = resp.text
        assert "Traceback" not in text
        assert "SELECT" not in text.upper() or "select" not in text
        assert ".py" not in text


@pytest.mark.unit
async def test_sec03_task_ids_client_generated(client, alice):
    """SEC-03: Task IDs are client-generated and follow t-<uuid4>."""
    for _ in range(5):
        task = await create_task(client, alice)
        raw = task["task_id"].removeprefix("t-")
        assert UUID4_RE.match(raw), f"Invalid task_id format: {task['task_id']}"


@pytest.mark.unit
async def test_sec04_bid_ids_format(client, alice, bob, carol):
    """SEC-04: Bid IDs follow bid-<uuid4>."""
    task = await create_task(client, alice)
    bid1 = await submit_bid(client, bob, task["task_id"])
    bid2 = await submit_bid(client, carol, task["task_id"])
    for bid in [bid1, bid2]:
        raw = bid["bid_id"].removeprefix("bid-")
        assert UUID4_RE.match(raw), f"Invalid bid_id format: {bid['bid_id']}"


@pytest.mark.unit
async def test_sec07_cross_action_replay(client, alice, bob):
    """SEC-07: Cross-action token replay is rejected."""
    task = await create_task(client, alice)
    bid_payload = {
        "action": "submit_bid",
        "task_id": task["task_id"],
        "bidder_id": bob.agent_id,
        "proposal": "Test",
    }
    bid_token = make_jws(bob, bid_payload)

    # Submit bid normally
    await client.post(f"/tasks/{task['task_id']}/bids", json={"token": bid_token})

    # Replay bid token against submit endpoint
    resp = await client.post(f"/tasks/{task['task_id']}/submit", json={"token": bid_token})
    assert resp.status_code == 400
    assert resp.json()["error"] == "INVALID_PAYLOAD"


@pytest.mark.unit
async def test_sec08_sql_injection_paths(client):
    """SEC-08: SQL injection in path parameters returns 404."""
    injection_paths = [
        "/tasks/' OR '1'='1",
        "/tasks/' OR '1'='1/bids",
        "/tasks/' OR '1'='1/assets",
    ]
    for path in injection_paths:
        resp = await client.get(path)
        assert resp.status_code == 404, f"Expected 404 for {path}"
        text = resp.text
        assert "SQL" not in text.upper()
        assert "Traceback" not in text


@pytest.mark.unit
async def test_sec09_path_traversal_assets(client, alice, bob):
    """SEC-09: Path traversal in asset download returns 404."""
    task = await create_task(client, alice)
    bid = await submit_bid(client, bob, task["task_id"])
    await accept_bid(client, alice, task["task_id"], bid["bid_id"])

    traversal_paths = [
        f"/tasks/{task['task_id']}/assets/../../etc/passwd",
        f"/tasks/{task['task_id']}/assets/../../../config.yaml",
    ]
    for path in traversal_paths:
        resp = await client.get(path)
        assert resp.status_code == 404, f"Expected 404 for {path}"
```

**Remaining:** SEC-05 (asset ID format), SEC-06 (escrow ID format).

---

## File 22: `tests/integration/conftest.py`

Create this file:

```python
"""Integration test fixtures."""
```

---

## File 23: `tests/integration/test_endpoints.py`

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

## File 24: `tests/performance/conftest.py`

Create this file:

```python
"""Performance test fixtures."""
```

---

## File 25: `tests/performance/test_performance.py`

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

## Implementing Remaining Tests

After creating all files above, implement the remaining tests by following these rules:

1. **Every test** is marked with `@pytest.mark.unit` and is `async def test_...`
2. **Use the helper functions** from `conftest.py`: `create_task`, `submit_bid`, `accept_bid`, `upload_asset`, `submit_deliverable`, `full_lifecycle_to_submitted`, `full_lifecycle_to_disputed`, `make_jws`, `make_tampered_jws`, `make_keypair`
3. **For mock control**: access `identity_mock_state` or `central_bank_mock_state` fixtures to force errors (e.g., `identity_mock_state.force_unavailable = True` for TC-22)
4. **For parameterized validation tests** (TC-13, TC-14a/b/c, RUL-06): loop over invalid values and assert each one
5. **For deadline tests** (LIFE-03 to LIFE-08): use `bidding_deadline_seconds=1` or similar, then `await asyncio.sleep(2)`, then read via GET
6. **For concurrent tests** (BID-14, LIFE-08): use `asyncio.gather` with multiple client calls
7. **For escrow rollback** (TC-28): access the app state to get the DB path, pre-insert a row with the target `task_id` directly via `aiosqlite`, then attempt creation and verify `central_bank_mock_state.release_calls` was called
8. **For error precedence** (PREC-01 to PREC-10): configure mocks and send requests with multiple error conditions; assert the first-in-precedence error wins
9. **Test naming**: use `test_<id>_<short_description>` (e.g., `test_tc09_missing_fields`)
10. **Imports**: always import helpers from `tests.unit.conftest`

### Remaining Test Count by File

| File | Tests Written | Tests Remaining | Total |
|------|--------------|-----------------|-------|
| test_health.py | 4 | 0 | 4 |
| test_tasks.py | 16 | 27 | 43 |
| test_cancel.py | 5 | 4 | 9 |
| test_bids.py | 10 | 13 | 23 |
| test_bid_accept.py | 4 | 6 | 10 |
| test_assets.py | 7 | 10 | 17 |
| test_submit.py | 4 | 5 | 9 |
| test_approve.py | 3 | 6 | 9 |
| test_dispute.py | 4 | 6 | 10 |
| test_ruling.py | 4 | 9 | 13 |
| test_lifecycle.py | 6 | 6 | 12 |
| test_http_methods.py | 1 | 0 | 1 |
| test_error_precedence.py | 7 | 3 | 10 |
| test_security.py | 6 | 3 | 9 |
| **Total** | **81** | **98** | **179** |

---

## Verification

```bash
cd services/task-board && just ci-quiet
```

All CI checks must pass. Tests are expected to fail (service is not yet implemented) but must be syntactically valid and CI-compliant (formatting, linting, type checking, spelling).

If tests fail to import:
1. Verify `tests/unit/routers/__init__.py` exists
2. Verify `conftest.py` fixtures are properly scoped
3. Verify `CONFIG_PATH` is set before importing app modules

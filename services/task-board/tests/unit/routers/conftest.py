"""Router test fixtures with mocked Identity and Central Bank services."""

from __future__ import annotations

import base64
import json
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from task_board_service.app import create_app
from task_board_service.config import clear_settings_cache
from task_board_service.core.lifespan import lifespan
from task_board_service.core.state import get_app_state, reset_app_state
from tests.helpers import generate_keypair, make_jws_token

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

# ---------------------------------------------------------------------------
# Fixed agent IDs
# ---------------------------------------------------------------------------
PLATFORM_AGENT_ID = "a-platform-test-id"
ALICE_AGENT_ID = "a-alice-uuid"
BOB_AGENT_ID = "a-bob-uuid"
CAROL_AGENT_ID = "a-carol-uuid"


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------
def make_agent_id() -> str:
    """Generate a unique agent ID."""
    return f"a-{uuid.uuid4()}"


def make_task_id() -> str:
    """Generate a unique task ID."""
    return f"t-{uuid.uuid4()}"


def make_bid_id() -> str:
    """Generate a unique bid ID."""
    return f"bid-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# Keypair fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def platform_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate a platform keypair."""
    return generate_keypair()


@pytest.fixture
def alice_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate Alice's keypair."""
    return generate_keypair()


@pytest.fixture
def bob_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate Bob's keypair."""
    return generate_keypair()


@pytest.fixture
def carol_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate Carol's keypair."""
    return generate_keypair()


@pytest.fixture
def platform_agent_id() -> str:
    """Return the platform agent ID."""
    return PLATFORM_AGENT_ID


@pytest.fixture
def alice_agent_id() -> str:
    """Return Alice's agent ID."""
    return ALICE_AGENT_ID


@pytest.fixture
def bob_agent_id() -> str:
    """Return Bob's agent ID."""
    return BOB_AGENT_ID


@pytest.fixture
def carol_agent_id() -> str:
    """Return Carol's agent ID."""
    return CAROL_AGENT_ID


# ---------------------------------------------------------------------------
# App + client fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
async def app(tmp_path: Path) -> AsyncIterator[Any]:
    """Create a test app with temp database and mocked external services."""
    db_path = tmp_path / "test.db"
    config_content = f"""\
service:
  name: "task-board"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8003
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
database:
  path: "{db_path}"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
central_bank:
  base_url: "http://localhost:8002"
  escrow_lock_path: "/escrow/lock"
  escrow_release_path: "/escrow/release"
  escrow_split_path: "/escrow/split"
  timeout_seconds: 10
platform:
  agent_id: "{PLATFORM_AGENT_ID}"
request:
  max_body_size: 1048576
deadlines:
  default_bidding_seconds: 3600
  default_execution_seconds: 86400
  default_review_seconds: 86400
limits:
  max_title_length: 200
  max_spec_length: 10000
  max_reason_length: 2000
  max_file_size: 10485760
  max_assets_per_task: 20
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)

    old_config = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    test_app = create_app()
    async with lifespan(test_app):
        # Replace external service clients with mocks
        state = get_app_state()

        # Mock Identity client — default: verification succeeds
        mock_identity = AsyncMock()
        mock_identity.close = AsyncMock()
        state.identity_client = mock_identity

        # Mock Central Bank client — default: escrow operations succeed
        mock_bank = AsyncMock()
        mock_bank.close = AsyncMock()
        mock_bank.escrow_lock = AsyncMock(
            return_value={"escrow_id": f"esc-{uuid.uuid4()}", "status": "locked"}
        )
        mock_bank.escrow_release = AsyncMock(return_value={"status": "released"})
        mock_bank.escrow_split = AsyncMock(return_value={"status": "split"})
        state.central_bank_client = mock_bank

        # Propagate mocks to extracted services
        if state.task_manager is not None:
            state.task_manager._identity_client = mock_identity
            state.task_manager._central_bank_client = mock_bank
        if state.token_validator is not None:
            state.token_validator._identity_client = mock_identity
        if state.escrow_coordinator is not None:
            state.escrow_coordinator._central_bank_client = mock_bank

        yield test_app

    reset_app_state()
    clear_settings_cache()
    if old_config is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = old_config


@pytest.fixture
async def client(app: Any) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Mock override fixtures (use these to replace default mock behavior)
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_identity_verify_success(_app: Any) -> None:
    """Configure the Identity mock to verify JWS successfully.

    The default mock already returns a truthy AsyncMock response.
    This fixture makes the behaviour explicit when tests depend on it.
    """
    state = get_app_state()
    state.identity_client.verify_jws = AsyncMock(
        side_effect=lambda token: {
            "valid": True,
            "agent_id": _extract_kid(token),
            "payload": _extract_payload(token),
        }
    )


@pytest.fixture
def mock_identity_unavailable(_app: Any) -> None:
    """Configure the Identity mock to simulate service unavailability."""
    state = get_app_state()
    state.identity_client.verify_jws = AsyncMock(
        side_effect=ConnectionError("Identity service unreachable")
    )


@pytest.fixture
def mock_identity_timeout(_app: Any) -> None:
    """Configure the Identity mock to simulate a timeout."""
    state = get_app_state()
    state.identity_client.verify_jws = AsyncMock(
        side_effect=TimeoutError("Identity service timed out")
    )


@pytest.fixture
def mock_identity_unexpected_response(_app: Any) -> None:
    """Configure the Identity mock to return an unexpected response."""
    state = get_app_state()
    state.identity_client.verify_jws = AsyncMock(
        side_effect=ValueError("Unexpected response from Identity service")
    )


@pytest.fixture
def mock_central_bank_insufficient_funds(_app: Any) -> None:
    """Configure the Central Bank mock to reject escrow with insufficient funds."""
    state = get_app_state()
    state.central_bank_client.escrow_lock = AsyncMock(side_effect=Exception("INSUFFICIENT_FUNDS"))


@pytest.fixture
def mock_central_bank_unavailable(_app: Any) -> None:
    """Configure the Central Bank mock to simulate unavailability."""
    state = get_app_state()
    state.central_bank_client.escrow_lock = AsyncMock(
        side_effect=ConnectionError("Central Bank unreachable")
    )
    state.central_bank_client.escrow_release = AsyncMock(
        side_effect=ConnectionError("Central Bank unreachable")
    )
    state.central_bank_client.escrow_split = AsyncMock(
        side_effect=ConnectionError("Central Bank unreachable")
    )


# ---------------------------------------------------------------------------
# JWS helper utilities (used by mock_identity_verify_success)
# ---------------------------------------------------------------------------


def _extract_kid(token: str) -> str:
    """Extract the kid (agent_id) from a JWS compact token header."""
    header_b64 = token.split(".", maxsplit=1)[0]
    # Add padding
    padded = header_b64 + "=" * (4 - len(header_b64) % 4)
    header = json.loads(base64.urlsafe_b64decode(padded))
    return header.get("kid", "unknown")


def _extract_payload(token: str) -> dict[str, Any]:
    """Extract the payload from a JWS compact token."""
    payload_b64 = token.split(".")[1]
    padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


# ---------------------------------------------------------------------------
# Task lifecycle helper functions
# ---------------------------------------------------------------------------
async def create_task(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    *,
    task_id: str | None = None,
    title: str = "Test task",
    spec: str = "Test specification for task",
    reward: int = 100,
    bidding_deadline_seconds: int = 3600,
    execution_deadline_seconds: int = 86400,
    review_deadline_seconds: int = 86400,
) -> Any:
    """Create a task via POST /tasks and return the response."""
    if task_id is None:
        task_id = make_task_id()

    private_key = poster_keypair[0]

    task_payload = {
        "action": "create_task",
        "task_id": task_id,
        "poster_id": poster_id,
        "title": title,
        "spec": spec,
        "reward": reward,
        "bidding_deadline_seconds": bidding_deadline_seconds,
        "execution_deadline_seconds": execution_deadline_seconds,
        "review_deadline_seconds": review_deadline_seconds,
    }
    task_token = make_jws_token(private_key, poster_id, task_payload)

    escrow_payload = {
        "action": "escrow_lock",
        "task_id": task_id,
        "agent_id": poster_id,
        "amount": reward,
    }
    escrow_token = make_jws_token(private_key, poster_id, escrow_payload)

    return await client.post(
        "/tasks",
        json={"task_token": task_token, "escrow_token": escrow_token},
    )


async def submit_bid(
    client: AsyncClient,
    bidder_keypair: tuple[Ed25519PrivateKey, str],
    bidder_id: str,
    task_id: str,
    *,
    amount: int = 90,
) -> Any:
    """Submit a bid via POST /tasks/{task_id}/bids and return the response."""
    private_key = bidder_keypair[0]
    payload = {
        "action": "submit_bid",
        "task_id": task_id,
        "bidder_id": bidder_id,
        "amount": amount,
    }
    token = make_jws_token(private_key, bidder_id, payload)
    return await client.post(f"/tasks/{task_id}/bids", json={"token": token})


async def accept_bid(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    task_id: str,
    bid_id: str,
) -> Any:
    """Accept a bid via POST /tasks/{task_id}/bids/{bid_id}/accept."""
    private_key = poster_keypair[0]
    payload = {
        "action": "accept_bid",
        "task_id": task_id,
        "bid_id": bid_id,
        "poster_id": poster_id,
    }
    token = make_jws_token(private_key, poster_id, payload)
    return await client.post(f"/tasks/{task_id}/bids/{bid_id}/accept", json={"token": token})


async def upload_asset(
    client: AsyncClient,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
    task_id: str,
    *,
    filename: str = "test.txt",
    content: bytes = b"test file content",
) -> Any:
    """Upload an asset via POST /tasks/{task_id}/assets with Bearer auth."""
    private_key = worker_keypair[0]
    payload = {
        "action": "upload_asset",
        "task_id": task_id,
    }
    token = make_jws_token(private_key, worker_id, payload)
    return await client.post(
        f"/tasks/{task_id}/assets",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (filename, content, "application/octet-stream")},
    )


async def submit_deliverable(
    client: AsyncClient,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
    task_id: str,
) -> Any:
    """Submit deliverable via POST /tasks/{task_id}/submit."""
    private_key = worker_keypair[0]
    payload = {
        "action": "submit_deliverable",
        "task_id": task_id,
        "worker_id": worker_id,
    }
    token = make_jws_token(private_key, worker_id, payload)
    return await client.post(f"/tasks/{task_id}/submit", json={"token": token})


async def approve_task(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    task_id: str,
) -> Any:
    """Approve deliverable via POST /tasks/{task_id}/approve."""
    private_key = poster_keypair[0]
    payload = {
        "action": "approve_task",
        "task_id": task_id,
        "poster_id": poster_id,
    }
    token = make_jws_token(private_key, poster_id, payload)
    return await client.post(f"/tasks/{task_id}/approve", json={"token": token})


async def file_dispute(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    task_id: str,
    *,
    reason: str = "Work does not meet specification",
) -> Any:
    """File a dispute via POST /tasks/{task_id}/dispute."""
    private_key = poster_keypair[0]
    payload = {
        "action": "file_dispute",
        "task_id": task_id,
        "poster_id": poster_id,
        "reason": reason,
    }
    token = make_jws_token(private_key, poster_id, payload)
    return await client.post(f"/tasks/{task_id}/dispute", json={"token": token})


async def submit_ruling(
    client: AsyncClient,
    platform_keypair: tuple[Ed25519PrivateKey, str],
    platform_id: str,
    task_id: str,
    *,
    worker_pct: int = 50,
    ruling_summary: str = "Split ruling",
) -> Any:
    """Submit a ruling via POST /tasks/{task_id}/ruling."""
    private_key = platform_keypair[0]
    payload = {
        "action": "submit_ruling",
        "task_id": task_id,
        "worker_pct": worker_pct,
        "ruling_summary": ruling_summary,
    }
    token = make_jws_token(private_key, platform_id, payload)
    return await client.post(f"/tasks/{task_id}/ruling", json={"token": token})


async def setup_task_in_execution(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
) -> tuple[str, str]:
    """Create a task and advance it to EXECUTION status.

    Returns (task_id, bid_id).
    """
    task_id = make_task_id()
    await create_task(client, poster_keypair, poster_id, task_id=task_id)

    bid_resp = await submit_bid(client, worker_keypair, worker_id, task_id)
    bid_id = bid_resp.json()["bid_id"]

    await accept_bid(client, poster_keypair, poster_id, task_id, bid_id)
    return task_id, bid_id


async def setup_task_in_review(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
) -> str:
    """Create a task and advance it to REVIEW status.

    Returns the task_id.
    """
    task_id, _bid_id = await setup_task_in_execution(
        client, poster_keypair, poster_id, worker_keypair, worker_id
    )
    await upload_asset(client, worker_keypair, worker_id, task_id)
    await submit_deliverable(client, worker_keypair, worker_id, task_id)
    return task_id


async def setup_task_in_dispute(
    client: AsyncClient,
    poster_keypair: tuple[Ed25519PrivateKey, str],
    poster_id: str,
    worker_keypair: tuple[Ed25519PrivateKey, str],
    worker_id: str,
) -> str:
    """Create a task and advance it to DISPUTED status.

    Returns the task_id.
    """
    task_id = await setup_task_in_review(
        client, poster_keypair, poster_id, worker_keypair, worker_id
    )
    await file_dispute(client, poster_keypair, poster_id, task_id)
    return task_id

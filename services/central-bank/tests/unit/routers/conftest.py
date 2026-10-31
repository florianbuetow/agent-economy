"""Router test fixtures with mocked Identity service."""

from __future__ import annotations

import base64
import json
import os
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient
from service_auth.jws import JwsAdmissionError, ParsedJws, VerifiedJws, create_jws, parse_jws
from service_auth.platform import PlatformAgent
from service_auth.signing import generate_keypair
from service_commons.exceptions import ServiceError

from central_bank_service.app import create_app
from central_bank_service.config import clear_settings_cache
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.state import get_app_state, reset_app_state
from tests.fakes.in_memory_ledger_store import InMemoryLedgerStore

if TYPE_CHECKING:
    from pathlib import Path

PLATFORM_AGENT_ID = "a-platform-test-id"


def make_operation_id() -> str:
    """Allocate one explicit caller-owned financial intent for a test action."""
    return f"op-{uuid4()}"


async def _register_platform_agent(agent: PlatformAgent) -> None:
    """Register a fixture platform identity without an external Identity service."""
    agent.agent_id = PLATFORM_AGENT_ID


def _write_agent_config(tmp_path: Path) -> Path:
    """Provision a disposable platform identity for load-only startup."""
    keys_dir = tmp_path / "keys"
    generate_keypair("platform", keys_dir)
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text('agents:\n  platform:\n    name: "Platform"\n')
    config_path = tmp_path / "agents.yaml"
    config_path.write_text(
        f"""\
platform:
  identity_url: "http://localhost:8001"
  bank_url: "http://localhost:8002"
  task_board_url: "http://localhost:8003"
  reputation_url: "http://localhost:8004"
  court_url: "http://localhost:8005"
data:
  keys_dir: "{keys_dir}"
  roster_path: "{roster_path}"
signing:
  token_ttl_seconds: 60
  max_lifetime_seconds: 60
  clock_skew_seconds: 0
""",
    )
    return config_path


def _generate_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Generate an Ed25519 keypair, returning (private_key, formatted_public_key)."""
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes_raw()
    public_key = f"ed25519:{base64.b64encode(pub_bytes).decode()}"
    return private_key, public_key


def _resource_for_payload(payload: dict[str, Any], agent_id: str) -> str:
    action = payload.get("action")
    if action == "create_account" or {"agent_id", "initial_balance"} <= payload.keys():
        resource = "POST /accounts"
    elif action == "escrow_lock" or {"agent_id", "task_id"} <= payload.keys():
        resource = "POST /escrow/lock"
    elif action == "credit" or "reference" in payload:
        resource = f"POST /accounts/{payload.get('account_id')}/credit"
    elif action == "get_balance":
        resource = f"GET /accounts/{payload.get('account_id') or agent_id}"
    elif action == "get_transactions":
        resource = f"GET /accounts/{payload.get('account_id') or agent_id}/transactions"
    elif action == "escrow_release" or "recipient_account_id" in payload:
        resource = f"POST /escrow/{payload.get('escrow_id')}/release"
    elif action == "escrow_settle":
        resource = f"POST /escrow/{payload.get('escrow_id')}/settle"
    elif action == "escrow_split" or "worker_pct" in payload:
        resource = f"POST /escrow/{payload.get('escrow_id')}/split"
    else:
        resource = "POST /accounts"
    return resource


def make_jws_token(
    private_key: Ed25519PrivateKey,
    agent_id: str,
    payload: dict[str, Any],
    *,
    resource: str | None = None,
) -> str:
    """Create a strict JWS token targeting the route exercised by the test."""
    selected_resource = resource or _resource_for_payload(payload, agent_id)
    if not isinstance(payload.get("action"), str):
        issued_at = int(time.time())
        header = {
            "alg": "EdDSA",
            "typ": "JWT",
            "kid": agent_id,
            "iat": issued_at,
            "exp": issued_at + 60,
            "aud": "central-bank",
            "resource": selected_resource,
        }
        header_segment = base64.urlsafe_b64encode(
            json.dumps(header, sort_keys=True, separators=(",", ":")).encode()
        ).rstrip(b"=")
        payload_segment = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).rstrip(b"=")
        signing_input = header_segment + b"." + payload_segment
        signature = base64.urlsafe_b64encode(private_key.sign(signing_input)).rstrip(b"=")
        return b".".join((header_segment, payload_segment, signature)).decode("ascii")
    return create_jws(
        payload,
        private_key,
        kid=agent_id,
        audience="central-bank",
        resource=selected_resource,
        token_ttl_seconds=60,
    )


def _decode_jws(token: str) -> VerifiedJws:
    """Return the complete typed token result for domain-focused router tests."""
    parsed: ParsedJws = parse_jws(token)
    return VerifiedJws(header=parsed.header, payload=parsed.payload)


def _make_delegating_verify_jws(state_ref: Any) -> Any:
    """Create a verify_jws mock that delegates to platform_agent.validate_certificate.

    This ensures tests that override validate_certificate (e.g. with InvalidSignature)
    will see the same behavior when verification goes through identity_client.verify_jws.

    Signature-related errors (InvalidSignature, ValueError) are mapped to valid=False.
    Connectivity errors (ConnectionError, TimeoutError, etc.) are raised as ServiceError(502)
    to simulate what the real IdentityClient would do.
    """

    async def _delegating_verify_jws(token: str) -> VerifiedJws:
        try:
            return state_ref.platform_agent.validate_certificate(token)
        except InvalidSignature:
            raise InvalidSignature from None
        except JwsAdmissionError:
            raise
        except Exception as exc:
            raise ServiceError(
                "identity_service_unavailable",
                "Cannot reach Identity service",
                502,
                {},
            ) from exc

    return _delegating_verify_jws


@pytest.fixture
def platform_keypair():
    """Generate a platform keypair."""
    return _generate_keypair()


@pytest.fixture
def agent_keypair():
    """Generate an agent keypair."""
    return _generate_keypair()


@pytest.fixture
async def app(tmp_path, monkeypatch):
    """Create a test app with a temporary database and mocked Identity client."""
    db_path = tmp_path / "test.db"
    generate_keypair("gateway-central-bank", tmp_path)
    agent_config_path = _write_agent_config(tmp_path)
    config_content = f"""
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8002
  log_level: "info"
telemetry:
  enabled: false
  otlp_endpoint: "http://127.0.0.1:4318"
  sample_ratio: 1.0
  excluded_urls: "health"
logging:
  level: "WARNING"
  directory: "{tmp_path / "logs" / "central-bank"}"
identity:
  base_url: "http://localhost:8001"
  get_agent_path: "/agents"
  verify_jws_path: "/agents/verify-jws"
  timeout_seconds: 10
platform:
  agent_config_path: "{agent_config_path}"
request:
  max_body_size: 1048576
db_gateway:
  url: "http://localhost:8007"
  timeout_seconds: 10
  signing:
    kid: "gateway-central-bank"
    private_key_path: "gateway-central-bank.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()
    monkeypatch.setattr(PlatformAgent, "register", _register_platform_agent)

    test_app = create_app()
    async with lifespan(test_app):
        # Replace runtime clients with mocks
        state = get_app_state()
        state.ledger = InMemoryLedgerStore(db_path=str(db_path))
        await state.platform_agent.close()
        mock_identity = AsyncMock()
        mock_identity.close = AsyncMock()
        mock_identity.get_agent = AsyncMock(
            return_value={"agent_id": "a-test-agent", "name": "Test Agent"},
        )
        state.identity_client = mock_identity
        mock_platform = MagicMock()
        mock_platform.agent_id = PLATFORM_AGENT_ID
        mock_platform.validate_certificate = MagicMock(side_effect=_decode_jws)
        mock_platform.close = AsyncMock()
        state.platform_agent = mock_platform
        mock_identity.verify_jws = AsyncMock(side_effect=_make_delegating_verify_jws(state))

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

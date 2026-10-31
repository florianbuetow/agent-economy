"""Unit test fixtures."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient
from service_auth.gateway import GatewayRequestSigner, GatewaySignerConfig
from service_auth.signing import generate_keypair

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
        current_task_board_caller_kid="gateway-task-board",  # type: ignore[call-arg]
    )
    yield writer
    writer.close()


@pytest.fixture
def gateway_test_signers(tmp_path: Path) -> dict[str, GatewayRequestSigner]:
    """Provision distinct ephemeral service credentials for router tests."""
    return _create_gateway_keys(tmp_path)


@pytest.fixture
def app_with_writer(
    db_writer: DbWriter,
    gateway_test_signers: dict[str, GatewayRequestSigner],
    tmp_path: Path,
) -> Iterator[TestClient]:
    """Create a FastAPI test client with a real DbWriter."""
    config_path = tmp_path / "config.yaml"
    _create_test_config(db_writer._db_path, config_path)

    from db_gateway_service.app import create_app

    os.environ["CONFIG_PATH"] = str(config_path)
    clear_settings_cache()
    app = create_app()
    with _GatewayTestClient(app, gateway_auth=_GatewayTestAuth(gateway_test_signers)) as client:
        state = init_app_state()
        state.db_writer = db_writer
        yield client

    if "CONFIG_PATH" in os.environ:
        os.environ.pop("CONFIG_PATH")


class _GatewayTestAuth(httpx.Auth):
    """Give legacy domain tests explicit least-privilege service credentials."""

    def __init__(self, signers: dict[str, GatewayRequestSigner]) -> None:
        self._signers = signers

    def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        domain = request.url.path.split("/", maxsplit=2)[1]
        if domain == "health" and request.method == "GET" and not request.url.query:
            yield request
            return
        if domain == "health":
            domain = "identity"
        signer = self._signers[domain]
        access = "read" if request.method == "GET" else "write"
        yield signer.sign_request(request, action=f"gateway.{domain}.{access}")


class _GatewayTestClient(TestClient):
    """Apply explicit service auth while preserving per-request auth overrides."""

    def __init__(self, app: object, *, gateway_auth: httpx.Auth) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._auth = gateway_auth


def _create_gateway_keys(config_dir: Path) -> dict[str, GatewayRequestSigner]:
    """Provision distinct ephemeral service credentials for router tests."""
    handles = {
        "identity": "gateway-identity",
        "bank": "gateway-central-bank",
        "board": "gateway-task-board",
        "reputation": "gateway-reputation",
        "court": "gateway-court",
    }
    signers: dict[str, GatewayRequestSigner] = {}
    for domain, handle in handles.items():
        generate_keypair(handle, config_dir)
        signers[domain] = GatewayRequestSigner(
            GatewaySignerConfig(
                kid=handle,
                private_key_path=Path(f"{handle}.key"),
                audience="db-gateway",
                token_ttl_seconds=30,
            ),
            config_dir=config_dir,
        )
    return signers


def _create_test_config(db_path: str, config_path: Path) -> None:
    """Write a temporary config.yaml for testing."""
    generate_keypair("platform", config_path.parent)
    config_content = f"""
service:
  name: "db-gateway-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18006
  log_level: "warning"

telemetry:

  enabled: false

  otlp_endpoint: "http://127.0.0.1:4318"

  sample_ratio: 1.0

  excluded_urls: "health"

logging:
  level: "WARNING"
  directory: "{config_path.parent / "logs" / "db-gateway"}"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "../../docs/specifications/schema.sql"
  busy_timeout_ms: 5000
  journal_mode: "wal"

request:
  max_body_size: 1048576

recovery:
  maintenance_lock_path: "maintenance.lock"
  backup_root: "backups"

board_authority:
  platform_public_key_path: "platform.pub"
  deadline_actor_id: "system-task-board-deadline"

board_effect_recovery:
  current_task_board_caller_kid: "gateway-task-board"
  max_attempts_per_delivery: 8
  initial_backoff_seconds: 2
  max_backoff_seconds: 60
  lease_duration_seconds: 45
  evidence_interval_seconds: 60
  gateway_timeout_seconds: 10
  bank_timeout_seconds: 10

auth:
  audience: "db-gateway"
  max_lifetime_seconds: 30
  clock_skew_seconds: 0
  trusted_callers:
    gateway-identity:
      public_key_path: "gateway-identity.pub"
      capabilities: ["gateway.identity.read", "gateway.identity.write"]
    gateway-central-bank:
      public_key_path: "gateway-central-bank.pub"
      capabilities: ["gateway.bank.read", "gateway.bank.write"]
    gateway-task-board:
      public_key_path: "gateway-task-board.pub"
      capabilities: ["gateway.board.read", "gateway.board.write"]
    gateway-reputation:
      public_key_path: "gateway-reputation.pub"
      capabilities: ["gateway.reputation.read", "gateway.reputation.write"]
    gateway-court:
      public_key_path: "gateway-court.pub"
      capabilities: ["gateway.court.read", "gateway.court.write", "gateway.board.task.read"]
"""
    config_path.write_text(config_content)

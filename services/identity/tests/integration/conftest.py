"""Integration test fixtures — the real service app, backed by a real,
in-process DB Gateway.

Unlike tests/unit (which swaps AgentRegistry's store for an in-memory fake),
these tests keep the production `AgentDbClient` in place and redirect its
`GatewayClient` at a real, in-process DB Gateway ASGI app (a fresh temp
SQLite database per test, no network socket, no live-stack dependency) — the
WP-04 contract-suite pattern established by
services/central-bank/tests/unit/test_ledger_store_contract.py and
tests/integration/gateway_helpers.py.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast
from unittest.mock import patch

import pytest
from db_gateway_service.app import create_app as create_gateway_app
from db_gateway_service.config import clear_settings_cache as clear_gateway_settings_cache
from db_gateway_service.core.state import init_app_state as init_gateway_state
from db_gateway_service.core.state import reset_app_state as reset_gateway_state
from db_gateway_service.services.db_reader import DbReader
from db_gateway_service.services.db_writer import DbWriter
from httpx import ASGITransport, AsyncClient
from service_auth.signing import generate_keypair

from identity_service.app import create_app
from identity_service.config import clear_settings_cache, get_settings
from identity_service.core.lifespan import lifespan
from identity_service.core.state import get_app_state, reset_app_state

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

    from identity_service.services.agent_db_client import AgentDbClient

_SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"


def _write_gateway_config(db_path: str) -> str:
    """Write a temp db-gateway config.yaml pointing at an isolated SQLite file."""
    content = f"""
service:
  name: "db-gateway-identity-integration-test"
  version: "0.1.0"

server:
  host: "127.0.0.1"
  port: 18029
  log_level: "warning"

logging:
  level: "WARNING"
  directory: "data/logs"
  format: "json"

database:
  path: "{db_path}"
  schema_path: "{_SCHEMA_PATH}"
  busy_timeout_ms: 5000
  journal_mode: "wal"

recovery:
  maintenance_lock_path: "{Path(db_path).parent / "maintenance.lock"}"
  backup_root: "{Path(db_path).parent / "backups"}"

request:
  max_body_size: 1048576
auth:
  audience: "db-gateway"
  max_lifetime_seconds: 300
  clock_skew_seconds: 0
  trusted_callers:
    gateway-identity:
      public_key_path: "{Path(db_path).parent / "gateway-identity.pub"}"
      capabilities:
        - "gateway.identity.read"
        - "gateway.identity.write"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(content)
        return f.name


async def _build_gateway_app(db_path: str) -> tuple[FastAPI, DbWriter]:
    """Build a real, in-process DB Gateway ASGI app against a fresh SQLite file."""
    generate_keypair("gateway-identity", Path(db_path).parent)
    config_path = _write_gateway_config(db_path)
    reset_gateway_state()
    state = init_gateway_state()
    writer = DbWriter(
        db_path=db_path,
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=_SCHEMA_PATH.read_text(),
    )
    state.db_writer = writer
    state.db_reader = DbReader(db=writer.connection)

    with patch.dict("os.environ", {"CONFIG_PATH": config_path}):
        clear_gateway_settings_cache()
        gateway_app = create_gateway_app()
    # Deliberately do NOT clear the gateway's settings cache here: get_settings()
    # is an argument-less lru_cache, so clearing it now would force the next
    # get_settings() call (e.g. from the gateway's own exception handler while
    # this app is still in use, after CONFIG_PATH has been repointed at the
    # caller's own config) to re-read CONFIG_PATH and blow up trying to
    # validate the wrong service's config against this Settings model. The
    # next call to this function clears-then-rebuilds fresh regardless, so
    # cross-test isolation still holds.
    Path(config_path).unlink(missing_ok=True)
    return gateway_app, writer


@pytest.fixture
def gateway_db_path(tmp_path: Path) -> str:
    """Path to the SQLite file backing the in-process DB Gateway for this test."""
    return str(tmp_path / "gateway.db")


@pytest.fixture
async def app(tmp_path: Path, gateway_db_path: str) -> AsyncIterator[FastAPI]:
    """Real identity app; its production AgentDbClient is redirected at a real,
    in-process DB Gateway instead of the InMemoryAgentStore fake unit tests use.
    """
    config_content = """
service:
  name: "identity"
  version: "0.1.0"
server:
  host: "127.0.0.1"
  port: 8001
  log_level: "info"
logging:
  level: "WARNING"
  directory: "data/logs"
crypto:
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
token_validation:
  max_lifetime_seconds: 300
  clock_skew_seconds: 30
db_gateway:
  url: "http://gateway-test"
  timeout_seconds: 10
  signing:
    kid: "gateway-identity"
    private_key_path: "gateway-identity.key"
    audience: "db-gateway"
    token_ttl_seconds: 300
"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_content)
    old_config = os.environ.get("CONFIG_PATH")
    os.environ["CONFIG_PATH"] = str(config_path)

    clear_settings_cache()
    reset_app_state()

    gateway_app, gateway_writer = await _build_gateway_app(gateway_db_path)

    test_app = create_app()
    async with lifespan(test_app):
        get_settings()
        state = get_app_state()
        assert state.registry is not None

        agent_db_client = cast("AgentDbClient", state.registry._store)
        await agent_db_client._gateway._client.aclose()
        agent_db_client._gateway._client = AsyncClient(
            transport=ASGITransport(app=gateway_app), base_url="http://gateway-test"
        )

        yield test_app

    reset_app_state()
    clear_settings_cache()
    if old_config is None:
        os.environ.pop("CONFIG_PATH", None)
    else:
        os.environ["CONFIG_PATH"] = old_config
    gateway_writer.close()
    reset_gateway_state()


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Create an async HTTP client for the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

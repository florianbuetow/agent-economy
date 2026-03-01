from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.config import AgentConfig
from base_agent.factory import AgentFactory

IDENTITY_URL = "http://localhost:8001"
BANK_URL = "http://localhost:8002"


@pytest.fixture(scope="session", autouse=True)
def _require_identity_service() -> None:
    try:
        response = httpx.get(f"{IDENTITY_URL}/health", timeout=3.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.exit(f"Identity service not running at {IDENTITY_URL}: {exc}", returncode=1)


@pytest.fixture(scope="session", autouse=True)
def _require_bank_service() -> None:
    try:
        response = httpx.get(f"{BANK_URL}/health", timeout=3.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.exit(f"Central Bank service not running at {BANK_URL}: {exc}", returncode=1)


@pytest.fixture()
def agent_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name="E2E Test Agent",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url=IDENTITY_URL,
        bank_url="http://localhost:8002",
        task_board_url="http://localhost:8003",
        reputation_url="http://localhost:8004",
        court_url="http://localhost:8005",
    )


@pytest.fixture()
async def platform_agent():
    factory = AgentFactory(config_path=Path(__file__).resolve().parents[2] / "config.yaml")
    agent = factory.platform_agent()
    await agent.register()
    yield agent
    await agent.close()

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig
from base_agent.factory import AgentFactory

if TYPE_CHECKING:
    from base_agent.platform import PlatformAgent

IDENTITY_URL = "http://localhost:8001"
BANK_URL = "http://localhost:8002"
TASK_BOARD_URL = "http://localhost:8003"
REPUTATION_URL = "http://localhost:8004"
COURT_URL = "http://localhost:8005"


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


@pytest.fixture(scope="session", autouse=True)
def _require_task_board_service() -> None:
    try:
        response = httpx.get(f"{TASK_BOARD_URL}/health", timeout=3.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.exit(f"Task Board service not running at {TASK_BOARD_URL}: {exc}", returncode=1)


@pytest.fixture(scope="session", autouse=True)
def _require_reputation_service() -> None:
    try:
        response = httpx.get(f"{REPUTATION_URL}/health", timeout=3.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.exit(f"Reputation service not running at {REPUTATION_URL}: {exc}", returncode=1)


@pytest.fixture(scope="session", autouse=True)
def _require_court_service() -> None:
    try:
        response = httpx.get(f"{COURT_URL}/health", timeout=3.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.exit(f"Court service not running at {COURT_URL}: {exc}", returncode=1)


@pytest.fixture()
def agent_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    return AgentConfig(
        name="E2E Test Agent",
        private_key=private_key,
        public_key=private_key.public_key(),
        identity_url=IDENTITY_URL,
        bank_url=BANK_URL,
        task_board_url=TASK_BOARD_URL,
        reputation_url=REPUTATION_URL,
        court_url=COURT_URL,
    )


@pytest.fixture()
async def platform_agent() -> PlatformAgent:
    factory = AgentFactory(config_path=Path(__file__).resolve().parents[2] / "config.yaml")
    agent = factory.platform_agent()
    await agent.register()
    yield agent
    await agent.close()


@pytest.fixture()
def make_funded_agent(agent_config: AgentConfig, platform_agent: PlatformAgent):
    """Factory fixture that creates a registered agent with a funded account."""

    async def _make(name: str = "Test Agent", balance: int = 1000) -> BaseAgent:
        private_key = Ed25519PrivateKey.generate()
        config = AgentConfig(
            name=name,
            private_key=private_key,
            public_key=private_key.public_key(),
            identity_url=agent_config.identity_url,
            bank_url=agent_config.bank_url,
            task_board_url=agent_config.task_board_url,
            reputation_url=agent_config.reputation_url,
            court_url=agent_config.court_url,
        )
        agent = BaseAgent(config=config)
        await agent.register()
        assert agent.agent_id is not None
        await platform_agent.create_account(agent_id=agent.agent_id, initial_balance=balance)
        return agent

    return _make

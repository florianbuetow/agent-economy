from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig
from base_agent.factory import AgentFactory
from base_agent.signing import TokenPolicy

if TYPE_CHECKING:
    from base_agent.platform import PlatformAgent

IDENTITY_URL = os.environ.get("IDENTITY_URL", "http://localhost:8001")
BANK_URL = os.environ.get("BANK_URL", "http://localhost:8002")
TASK_BOARD_URL = os.environ.get("TASK_BOARD_URL", "http://localhost:8003")
REPUTATION_URL = os.environ.get("REPUTATION_URL", "http://localhost:8004")
COURT_URL = os.environ.get("COURT_URL", "http://localhost:8005")
UI_URL = os.environ.get("UI_URL", "http://localhost:8008")
AGENT_CONFIG_PATH = Path(
    os.environ.get("AGENT_CONFIG_PATH", str(Path(__file__).resolve().parents[2] / "config.yaml"))
)


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


@pytest.fixture(scope="session")
def _require_ui_service() -> None:
    """Not autouse: only WP-08's proxy-route tests hit the UI service, so this

    is opt-in (via ``pytestmark = pytest.mark.usefixtures(...)``) rather than
    a blanket precondition for every test in this directory.
    """
    try:
        response = httpx.get(f"{UI_URL}/health", timeout=3.0)
        response.raise_for_status()
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        pytest.exit(f"UI service not running at {UI_URL}: {exc}", returncode=1)


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
        token_ttl_seconds=300,
        token_policy=TokenPolicy(max_lifetime_seconds=300, clock_skew_seconds=30),
    )


@pytest.fixture()
async def platform_agent() -> PlatformAgent:
    factory = AgentFactory(config_path=AGENT_CONFIG_PATH)
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
            token_ttl_seconds=agent_config.token_ttl_seconds,
            token_policy=agent_config.token_policy,
        )
        agent = BaseAgent(config=config)
        await agent.register()
        assert agent.agent_id is not None
        await platform_agent.create_account(agent_id=agent.agent_id, initial_balance=balance)
        return agent

    return _make

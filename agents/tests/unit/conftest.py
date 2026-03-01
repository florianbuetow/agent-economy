import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.config import AgentConfig


@pytest.fixture()
def sample_config() -> AgentConfig:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return AgentConfig(
        name="Test Bot",
        private_key=private_key,
        public_key=public_key,
        identity_url="http://localhost:8001",
        bank_url="http://localhost:8002",
        task_board_url="http://localhost:8003",
        reputation_url="http://localhost:8004",
        court_url="http://localhost:8005",
    )

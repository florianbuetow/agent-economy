import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from base_agent.config import AgentConfig


@pytest.mark.unit
class TestAgentConfig:
    def test_creates_config(self) -> None:
        pk = Ed25519PrivateKey.generate()
        config = AgentConfig(
            name="Alice",
            private_key=pk,
            public_key=pk.public_key(),
            identity_url="http://localhost:8001",
            bank_url="http://localhost:8002",
            task_board_url="http://localhost:8003",
            reputation_url="http://localhost:8004",
            court_url="http://localhost:8005",
        )
        assert config.name == "Alice"
        assert config.identity_url == "http://localhost:8001"

    def test_config_is_frozen(self) -> None:
        pk = Ed25519PrivateKey.generate()
        config = AgentConfig(
            name="Alice",
            private_key=pk,
            public_key=pk.public_key(),
            identity_url="http://localhost:8001",
            bank_url="http://localhost:8002",
            task_board_url="http://localhost:8003",
            reputation_url="http://localhost:8004",
            court_url="http://localhost:8005",
        )
        with pytest.raises(AttributeError):
            config.name = "Bob"

"""AgentFactory — creates agents with keys loaded transparently."""

from __future__ import annotations

from pathlib import Path

import yaml
from service_commons.config import get_config_path as resolve_config_path

from base_agent.agent import BaseAgent
from base_agent.config import AgentConfig
from base_agent.platform import PlatformAgent
from base_agent.signing import generate_keypair, load_private_key, load_public_key


class AgentFactory:
    """Factory that creates agents with their keys loaded transparently.

    The factory knows where keys and roster are stored. Callers never
    deal with key paths — they just ask for an agent by handle.

    Args:
        config_path: Path to the agents config.yaml. If None, resolved
            via AGENT_CONFIG_PATH env var or default location.
        keys_dir: Override for the keys directory. If None, resolved
            from config.yaml's data.keys_dir.
    """

    def __init__(
        self,
        config_path: Path | None = None,
        keys_dir: Path | None = None,
    ) -> None:
        if config_path is None:
            config_path = resolve_config_path(
                env_var_name="AGENT_CONFIG_PATH",
                default_filename="config.yaml",
            )

        raw = yaml.safe_load(config_path.read_text())
        if not isinstance(raw, dict):
            msg = f"Invalid config file: {config_path}"
            raise ValueError(msg)

        self._config_path = config_path

        # Resolve keys directory
        if keys_dir is not None:
            self._keys_dir = keys_dir.resolve()
        else:
            cfg_keys_dir = Path(raw["data"]["keys_dir"])
            if not cfg_keys_dir.is_absolute():
                cfg_keys_dir = config_path.parent / cfg_keys_dir
            self._keys_dir = cfg_keys_dir.resolve()

        # Load roster
        roster_path = Path(raw["data"]["roster_path"])
        if not roster_path.is_absolute():
            roster_path = config_path.parent / roster_path
        roster_raw = yaml.safe_load(roster_path.read_text())
        if not isinstance(roster_raw, dict):
            msg = f"Invalid roster file: {roster_path}"
            raise ValueError(msg)
        self._roster: dict[str, dict[str, str]] = roster_raw["agents"]

        # Store service URLs
        self._identity_url: str = raw["platform"]["identity_url"]
        self._bank_url: str = raw["platform"]["bank_url"]
        self._task_board_url: str = raw["platform"]["task_board_url"]
        self._reputation_url: str = raw["platform"]["reputation_url"]
        self._court_url: str = raw["platform"]["court_url"]

    def _load_config(self, handle: str) -> AgentConfig:
        """Load an AgentConfig for the given roster handle."""
        if handle not in self._roster:
            msg = f"Agent '{handle}' not found in roster"
            raise KeyError(msg)

        entry = self._roster[handle]

        private_path = self._keys_dir / f"{handle}.key"
        public_path = self._keys_dir / f"{handle}.pub"
        if private_path.exists() and public_path.exists():
            private_key = load_private_key(private_path)
            public_key = load_public_key(public_path)
        else:
            private_key, public_key = generate_keypair(handle, self._keys_dir)

        return AgentConfig(
            name=entry["name"],
            private_key=private_key,
            public_key=public_key,
            identity_url=self._identity_url,
            bank_url=self._bank_url,
            task_board_url=self._task_board_url,
            reputation_url=self._reputation_url,
            court_url=self._court_url,
        )

    def create_agent(self, handle: str) -> BaseAgent:
        """Create a regular agent by roster handle.

        Args:
            handle: Agent handle from roster.yaml (e.g., "alice", "bob").

        Returns:
            A BaseAgent initialized with the agent's keys.

        Raises:
            KeyError: If the handle is not in the roster.
        """
        config = self._load_config(handle)
        return BaseAgent(config)

    def platform_agent(self) -> PlatformAgent:
        """Create the platform agent with privileged operations.

        Returns:
            A PlatformAgent initialized with the platform keypair.

        Raises:
            KeyError: If "platform" is not in the roster.
        """
        config = self._load_config("platform")
        return PlatformAgent(config)

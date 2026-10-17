"""AgentFactory — creates agents with keys loaded transparently."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from cryptography.hazmat.primitives import serialization
from service_commons.config import get_config_path as resolve_config_path

from service_auth.agent import BaseAgent
from service_auth.config import AgentConfig, _FileSettings
from service_auth.jws import TokenPolicy
from service_auth.platform import PlatformAgent
from service_auth.signing import adopt_keypair_permissions, generate_keypair, load_keypair
from service_auth.user_agent import UserAgent

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


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

        file_settings = _FileSettings(**raw)
        self._config_path = config_path

        # Resolve keys directory
        if keys_dir is not None:
            self._keys_dir = keys_dir.resolve()
        else:
            cfg_keys_dir = Path(file_settings.data.keys_dir)
            if not cfg_keys_dir.is_absolute():
                cfg_keys_dir = config_path.parent / cfg_keys_dir
            self._keys_dir = cfg_keys_dir.resolve()

        # Load roster
        roster_path = Path(file_settings.data.roster_path)
        if not roster_path.is_absolute():
            roster_path = config_path.parent / roster_path
        roster_raw = yaml.safe_load(roster_path.read_text())
        if not isinstance(roster_raw, dict):
            msg = f"Invalid roster file: {roster_path}"
            raise ValueError(msg)
        self._roster: dict[str, dict[str, str]] = roster_raw["agents"]

        # Store service URLs
        self._identity_url = file_settings.platform.identity_url
        self._bank_url = file_settings.platform.bank_url
        self._task_board_url = file_settings.platform.task_board_url
        self._reputation_url = file_settings.platform.reputation_url
        self._court_url = file_settings.platform.court_url
        self._token_ttl_seconds = file_settings.signing.token_ttl_seconds
        self._token_policy = TokenPolicy(
            max_lifetime_seconds=file_settings.signing.max_lifetime_seconds,
            clock_skew_seconds=file_settings.signing.clock_skew_seconds,
        )

    def provision_configured_keys(self) -> dict[str, str]:
        """Explicitly provision every configured roster handle."""
        return {
            handle: self._public_fingerprint(generate_keypair(handle, self._keys_dir)[1])
            for handle in sorted(self._roster)
        }

    def configured_key_targets(self) -> dict[str, Path]:
        """Return canonical public-key targets for the configured economic roster."""
        return {handle: self._keys_dir / f"{handle}.pub" for handle in sorted(self._roster)}

    def check_configured_keys(self) -> dict[str, str]:
        """Validate every configured roster handle without changing key material."""
        return {
            handle: self._public_fingerprint(load_keypair(handle, self._keys_dir)[1])
            for handle in sorted(self._roster)
        }

    def adopt_configured_key_permissions(self) -> dict[str, str]:
        """Explicitly harden permissions for every valid configured legacy pair."""
        return {
            handle: self._public_fingerprint(adopt_keypair_permissions(handle, self._keys_dir)[1])
            for handle in sorted(self._roster)
        }

    @staticmethod
    def _public_fingerprint(public_key: Ed25519PublicKey) -> str:
        raw_public = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return hashlib.sha256(raw_public).hexdigest()

    def _load_config(self, handle: str) -> AgentConfig:
        """Load an AgentConfig for the given roster handle."""
        if handle not in self._roster:
            msg = f"Agent '{handle}' not found in roster"
            raise KeyError(msg)

        entry = self._roster[handle]

        private_key, public_key = load_keypair(handle, self._keys_dir)

        return AgentConfig(
            name=entry["name"],
            private_key=private_key,
            public_key=public_key,
            identity_url=self._identity_url,
            bank_url=self._bank_url,
            task_board_url=self._task_board_url,
            reputation_url=self._reputation_url,
            court_url=self._court_url,
            token_ttl_seconds=self._token_ttl_seconds,
            token_policy=self._token_policy,
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

    def user_agent(self, handle: str) -> UserAgent:
        """Create the user agent for UI-driven operations.

        The user agent is a distinct economic identity from the platform
        agent (Q-2, docs/plans/2026-07-10-q2-operator-identity-decision.md):
        it has its own keypair and bank account, separating treasury power
        from browser-driven actions and making operator activity
        attributable in the economy.

        Args:
            handle: Roster handle whose keys and identity the agent uses
                (e.g. "operator").

        Returns:
            A UserAgent initialized with the given handle's keypair.

        Raises:
            KeyError: If the handle is not in the roster.
        """
        config = self._load_config(handle)
        return UserAgent(config)

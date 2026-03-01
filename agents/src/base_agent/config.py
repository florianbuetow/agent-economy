"""Configuration loading and agent configuration factory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict
from service_commons.config import get_config_path as resolve_config_path

from base_agent.signing import generate_keypair, load_private_key, load_public_key

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )


@dataclass(frozen=True)
class AgentConfig:
    """Runtime agent configuration with in-memory key material."""

    name: str
    private_key: Ed25519PrivateKey
    public_key: Ed25519PublicKey
    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str


class _PlatformUrls(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_url: str
    bank_url: str
    task_board_url: str
    reputation_url: str
    court_url: str


class _DataPaths(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys_dir: str
    roster_path: str


class _FileSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: _PlatformUrls
    data: _DataPaths


def load_agent_config(handle: str, config_path: Path | None = None) -> AgentConfig:
    """Load AgentConfig from YAML settings + roster for the requested handle."""
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

    roster_path = Path(file_settings.data.roster_path)
    if not roster_path.is_absolute():
        roster_path = config_path.parent / roster_path

    roster_raw = yaml.safe_load(roster_path.read_text())
    if not isinstance(roster_raw, dict):
        msg = f"Invalid roster file: {roster_path}"
        raise ValueError(msg)

    roster = roster_raw
    agent_entry = roster["agents"][handle]

    keys_dir = Path(file_settings.data.keys_dir)
    if not keys_dir.is_absolute():
        keys_dir = config_path.parent / keys_dir
    keys_dir = keys_dir.resolve()

    private_path = keys_dir / f"{handle}.key"
    public_path = keys_dir / f"{handle}.pub"
    if private_path.exists() and public_path.exists():
        private_key = load_private_key(private_path)
        public_key = load_public_key(public_path)
    else:
        private_key, public_key = generate_keypair(handle, keys_dir)

    return AgentConfig(
        name=agent_entry["name"],
        private_key=private_key,
        public_key=public_key,
        identity_url=file_settings.platform.identity_url,
        bank_url=file_settings.platform.bank_url,
        task_board_url=file_settings.platform.task_board_url,
        reputation_url=file_settings.platform.reputation_url,
        court_url=file_settings.platform.court_url,
    )

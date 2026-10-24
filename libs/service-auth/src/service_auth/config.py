"""Configuration loading and agent configuration factory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from service_commons.config import get_config_path as resolve_config_path

from service_auth.jws import TokenPolicy
from service_auth.signing import load_keypair

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
    token_ttl_seconds: int
    token_policy: TokenPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.token_policy, TokenPolicy):
            msg = "token_policy must be a TokenPolicy"
            raise ValueError(msg)
        if (
            isinstance(self.token_ttl_seconds, bool)
            or not isinstance(self.token_ttl_seconds, int)
            or self.token_ttl_seconds <= 0
        ):
            msg = "token_ttl_seconds must be a positive integer"
            raise ValueError(msg)
        if self.token_ttl_seconds > self.token_policy.max_lifetime_seconds:
            msg = "token_ttl_seconds must not exceed max_lifetime_seconds"
            raise ValueError(msg)


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


class _Signing(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    token_ttl_seconds: int
    max_lifetime_seconds: int
    clock_skew_seconds: int

    @model_validator(mode="after")
    def validate_policy(self) -> _Signing:
        policy = TokenPolicy(
            max_lifetime_seconds=self.max_lifetime_seconds,
            clock_skew_seconds=self.clock_skew_seconds,
        )
        if self.token_ttl_seconds <= 0 or self.token_ttl_seconds > policy.max_lifetime_seconds:
            msg = "token_ttl_seconds must be positive and not exceed max_lifetime_seconds"
            raise ValueError(msg)
        return self


class _FileSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    platform: _PlatformUrls
    data: _DataPaths
    signing: _Signing


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

    private_key, public_key = load_keypair(handle, keys_dir)

    return AgentConfig(
        name=agent_entry["name"],
        private_key=private_key,
        public_key=public_key,
        identity_url=file_settings.platform.identity_url,
        bank_url=file_settings.platform.bank_url,
        task_board_url=file_settings.platform.task_board_url,
        reputation_url=file_settings.platform.reputation_url,
        court_url=file_settings.platform.court_url,
        token_ttl_seconds=file_settings.signing.token_ttl_seconds,
        token_policy=TokenPolicy(
            max_lifetime_seconds=file_settings.signing.max_lifetime_seconds,
            clock_skew_seconds=file_settings.signing.clock_skew_seconds,
        ),
    )

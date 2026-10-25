"""Load the one roster-declared UI operator as a public-key-only read authority."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field
from service_auth.jws import TokenPolicy
from service_auth.signing import load_public_key, public_key_to_b64
from service_commons.config import load_yaml_config

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


@dataclass(frozen=True, slots=True)
class OperatorAssetAuthority:
    """Public trust material for read-only terminal asset inspection."""

    public_key: Ed25519PublicKey
    canonical_public_key: str
    token_policy: TokenPolicy


class _AgentDataProjection(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    keys_dir: str = Field(min_length=1)
    roster_path: str = Field(min_length=1)


class _AgentSigningProjection(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    max_lifetime_seconds: int = Field(gt=0)
    clock_skew_seconds: int = Field(ge=0)


class _AgentConfigProjection(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    data: _AgentDataProjection
    signing: _AgentSigningProjection


class _RosterEntryProjection(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)


class _RosterProjection(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    agents: dict[str, _RosterEntryProjection]


def load_operator_asset_authority(config_path: Path) -> OperatorAssetAuthority:
    """Resolve exactly one ``type: operator`` roster entry and load only its public key."""
    config = _AgentConfigProjection.model_validate(load_yaml_config(config_path))
    roster_path = Path(config.data.roster_path)
    if not roster_path.is_absolute():
        roster_path = config_path.parent / roster_path
    roster = _RosterProjection.model_validate(load_yaml_config(roster_path))
    operator_handles = [
        handle for handle, entry in roster.agents.items() if entry.type == "operator"
    ]
    if len(operator_handles) != 1:
        raise ValueError("agent roster must declare exactly one operator")
    handle = operator_handles[0]
    if handle in {".", ".."} or "/" in handle or "\\" in handle or not handle:
        raise ValueError("agent roster operator handle is unsafe")

    keys_dir = Path(config.data.keys_dir)
    if not keys_dir.is_absolute():
        keys_dir = config_path.parent / keys_dir
    public_key = load_public_key(keys_dir.resolve() / f"{handle}.pub")
    policy = TokenPolicy(
        max_lifetime_seconds=config.signing.max_lifetime_seconds,
        clock_skew_seconds=config.signing.clock_skew_seconds,
    )
    return OperatorAssetAuthority(
        public_key=public_key,
        canonical_public_key=f"ed25519:{public_key_to_b64(public_key)}",
        token_policy=policy,
    )


__all__ = ["OperatorAssetAuthority", "load_operator_asset_authority"]

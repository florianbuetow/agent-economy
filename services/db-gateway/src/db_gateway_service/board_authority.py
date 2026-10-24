"""Receiver-owned actor authority for native Task Board operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from service_auth.signing import load_public_key, public_key_to_b64

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from db_gateway_service.config import BoardAuthorityConfig


@dataclass(frozen=True)
class BoardAuthority:
    """Canonical economic and system actors accepted by the Gateway."""

    platform_public_key: str
    deadline_actor_id: str


def load_board_authority(
    config: BoardAuthorityConfig,
    *,
    config_dir: Path,
    trusted_public_key_owners: Mapping[str, str],
) -> BoardAuthority:
    """Load and validate Board authority without requiring Identity availability."""
    public_key_path = config.platform_public_key_path
    if not public_key_path.is_absolute():
        public_key_path = config_dir / public_key_path
    public_key = load_public_key(public_key_path.resolve())
    fingerprint = public_key_to_b64(public_key)
    trusted_owner = trusted_public_key_owners.get(fingerprint)
    if trusted_owner is not None:
        msg = (
            "Platform economic identity shares key material with trusted "
            f"Gateway caller {trusted_owner!r}"
        )
        raise ValueError(msg)
    return BoardAuthority(
        platform_public_key=f"ed25519:{fingerprint}",
        deadline_actor_id=config.deadline_actor_id,
    )

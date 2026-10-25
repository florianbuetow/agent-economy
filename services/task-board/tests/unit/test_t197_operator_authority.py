"""T-197 tests for the roster-bound public-only operator authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from service_auth.signing import generate_keypair, public_key_to_b64

from task_board_service.operator_authority import load_operator_asset_authority

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _config(tmp_path: Path, roster: dict[str, object]) -> Path:
    (tmp_path / "roster.yaml").write_text(yaml.safe_dump({"agents": roster}))
    path = tmp_path / "agents.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "data": {"keys_dir": "keys", "roster_path": "roster.yaml"},
                "signing": {"max_lifetime_seconds": 60, "clock_skew_seconds": 2},
            }
        )
    )
    return path


def test_operator_authority_loads_only_the_unique_operator_public_key(tmp_path: Path) -> None:
    private_key, public_key = generate_keypair("human", tmp_path / "keys")
    (tmp_path / "keys" / "human.key").unlink()
    config = _config(
        tmp_path,
        {
            "human": {"name": "Human", "type": "operator"},
            "platform": {"name": "Platform", "type": "platform"},
        },
    )

    authority = load_operator_asset_authority(config)

    assert authority.canonical_public_key == f"ed25519:{public_key_to_b64(public_key)}"
    assert authority.public_key.public_bytes_raw() == private_key.public_key().public_bytes_raw()
    assert authority.token_policy.max_lifetime_seconds == 60
    assert authority.token_policy.clock_skew_seconds == 2


@pytest.mark.parametrize(
    "roster",
    [
        {"platform": {"name": "Platform", "type": "platform"}},
        {
            "first": {"name": "First", "type": "operator"},
            "second": {"name": "Second", "type": "operator"},
        },
    ],
)
def test_operator_authority_requires_exactly_one_operator(
    tmp_path: Path,
    roster: dict[str, object],
) -> None:
    config = _config(tmp_path, roster)

    with pytest.raises(ValueError, match="exactly one operator"):
        load_operator_asset_authority(config)

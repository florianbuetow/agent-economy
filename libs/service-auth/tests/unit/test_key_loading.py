"""Acceptance tests for load-only AgentFactory and config consumers (T-114)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from service_auth.agent import BaseAgent
from service_auth.config import load_agent_config
from service_auth.factory import AgentFactory
from service_auth.platform import PlatformAgent
from service_auth.signing import (
    KeyMaterialError,
    generate_keypair,
    public_key_to_b64,
)
from service_auth.user_agent import UserAgent

if TYPE_CHECKING:
    from pathlib import Path


def _write_config(tmp_path: Path) -> tuple[Path, Path]:
    keys_dir = tmp_path / "keys"
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "alice": {"name": "Alice", "type": "worker"},
                    "operator": {"name": "Operator", "type": "operator"},
                    "platform": {"name": "Platform", "type": "platform"},
                }
            }
        )
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "platform": {
                    "identity_url": "http://identity.test",
                    "bank_url": "http://bank.test",
                    "task_board_url": "http://task-board.test",
                    "reputation_url": "http://reputation.test",
                    "court_url": "http://court.test",
                },
                "data": {
                    "keys_dir": str(keys_dir),
                    "roster_path": str(roster_path),
                },
                "signing": {
                    "token_ttl_seconds": 300,
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 30,
                },
            }
        )
    )
    return config_path, keys_dir


@pytest.mark.unit
@pytest.mark.parametrize(
    "consumer",
    ["create_agent", "platform_agent", "user_agent", "load_agent_config"],
)
def test_missing_keys_fail_without_implicit_generation(tmp_path: Path, consumer: str) -> None:
    config_path, keys_dir = _write_config(tmp_path)
    keys_dir.mkdir()
    factory = AgentFactory(config_path=config_path, keys_dir=keys_dir)

    with pytest.raises(KeyMaterialError) as error:
        if consumer == "create_agent":
            factory.create_agent("alice")
        elif consumer == "platform_agent":
            factory.platform_agent()
        elif consumer == "user_agent":
            factory.user_agent("operator")
        else:
            load_agent_config("alice", config_path)

    assert error.value.reason == "absent_pair"
    assert list(keys_dir.iterdir()) == []


@pytest.mark.unit
def test_preprovisioned_keys_load_for_every_supported_consumer(tmp_path: Path) -> None:
    config_path, keys_dir = _write_config(tmp_path)
    expected_fingerprints: dict[str, str] = {}
    for handle in ("alice", "operator", "platform"):
        _, public_key = generate_keypair(handle, keys_dir)
        expected_fingerprints[handle] = public_key_to_b64(public_key)

    factory = AgentFactory(config_path=config_path, keys_dir=keys_dir)
    alice = factory.create_agent("alice")
    platform = factory.platform_agent()
    operator = factory.user_agent("operator")
    loaded_config = load_agent_config("alice", config_path)

    assert isinstance(alice, BaseAgent)
    assert isinstance(platform, PlatformAgent)
    assert isinstance(operator, UserAgent)
    assert alice.get_public_key_b64() == expected_fingerprints["alice"]
    assert platform.get_public_key_b64() == expected_fingerprints["platform"]
    assert operator.get_public_key_b64() == expected_fingerprints["operator"]
    assert public_key_to_b64(loaded_config.public_key) == expected_fingerprints["alice"]


def _damage_keypair(keys_dir: Path, damage: str) -> str:
    generate_keypair("alice", keys_dir)
    if damage == "mismatch":
        other_dir = keys_dir.parent / "other-keys"
        generate_keypair("other", other_dir)
        (keys_dir / "alice.pub").write_bytes((other_dir / "other.pub").read_bytes())
        return "mismatched_pair"
    if damage == "insecure":
        (keys_dir / "alice.key").chmod(0o644)
        return "unsafe_mode"
    outside_public = keys_dir.parent / "outside.pub"
    outside_public.write_bytes((keys_dir / "alice.pub").read_bytes())
    (keys_dir / "alice.pub").unlink()
    (keys_dir / "alice.pub").symlink_to(outside_public)
    return "unsafe_path"


@pytest.mark.unit
@pytest.mark.parametrize("consumer", ["factory", "config"])
@pytest.mark.parametrize("damage", ["mismatch", "insecure", "symlink"])
def test_damaged_keys_fail_before_agent_http_initialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consumer: str,
    damage: str,
) -> None:
    config_path, keys_dir = _write_config(tmp_path)
    expected_reason = _damage_keypair(keys_dir, damage)

    def reject_http_initialization(*_args: object, **_kwargs: object) -> None:
        pytest.fail("agent HTTP client initialized before key validation")

    monkeypatch.setattr("service_auth.agent.httpx.AsyncClient", reject_http_initialization)

    with pytest.raises(KeyMaterialError) as error:
        if consumer == "factory":
            AgentFactory(config_path=config_path, keys_dir=keys_dir).create_agent("alice")
        else:
            load_agent_config("alice", config_path)

    assert error.value.reason == expected_reason

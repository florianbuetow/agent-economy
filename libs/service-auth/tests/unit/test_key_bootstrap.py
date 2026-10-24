"""Acceptance tests for explicit roster bootstrap and permission adoption (T-114)."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest
import yaml

from service_auth import signing
from service_auth.factory import AgentFactory
from service_auth.signing import KeyMaterialError, generate_keypair, public_key_to_b64

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )


def _adoption_api() -> Callable[
    [str, Path],
    tuple[Ed25519PrivateKey, Ed25519PublicKey],
]:
    adopter = getattr(signing, "adopt_keypair_permissions", None)
    assert callable(adopter), "MP-03 requires explicit adopt_keypair_permissions(handle, keys_dir)"
    return adopter


def _write_config(tmp_path: Path) -> tuple[Path, Path, tuple[str, ...]]:
    handles = ("alice", "operator", "platform")
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump(
            {"agents": {handle: {"name": handle.title(), "type": "worker"} for handle in handles}}
        )
    )
    keys_dir = tmp_path / "keys"
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
    return config_path, keys_dir, handles


@pytest.mark.unit
def test_explicit_adoption_hardens_valid_legacy_pair_without_rewriting_bytes(
    tmp_path: Path,
) -> None:
    _, original_public = generate_keypair("alice", tmp_path)
    private_path = tmp_path / "alice.key"
    public_path = tmp_path / "alice.pub"
    original_bytes = (private_path.read_bytes(), public_path.read_bytes())
    original_fingerprint = public_key_to_b64(original_public)
    private_path.chmod(0o644)

    with pytest.raises(KeyMaterialError) as strict_error:
        signing.load_keypair("alice", tmp_path)
    assert strict_error.value.reason == "unsafe_mode"
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o644

    adopter = _adoption_api()
    _, adopted_public = adopter("alice", tmp_path)
    adopter("alice", tmp_path)

    assert (private_path.read_bytes(), public_path.read_bytes()) == original_bytes
    assert public_key_to_b64(adopted_public) == original_fingerprint
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o600


@pytest.mark.unit
@pytest.mark.parametrize("damage", ["private-only", "corrupt", "mismatch"])
def test_explicit_adoption_refuses_invalid_material_without_modification(
    tmp_path: Path,
    damage: str,
) -> None:
    generate_keypair("alice", tmp_path)
    private_path = tmp_path / "alice.key"
    public_path = tmp_path / "alice.pub"
    private_path.chmod(0o644)
    if damage == "private-only":
        public_path.unlink()
    elif damage == "corrupt":
        public_path.write_bytes(b"invalid public key\n")
    else:
        other_dir = tmp_path / "other"
        generate_keypair("other", other_dir)
        public_path.write_bytes((other_dir / "other.pub").read_bytes())
    existing = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in (private_path, public_path)
        if path.exists()
    }

    adopter = _adoption_api()
    with pytest.raises(KeyMaterialError):
        adopter("alice", tmp_path)

    assert {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in (private_path, public_path)
        if path.exists()
    } == existing


@pytest.mark.unit
def test_factory_explicit_bootstrap_is_idempotent_and_check_is_load_only(tmp_path: Path) -> None:
    config_path, keys_dir, handles = _write_config(tmp_path)
    factory = AgentFactory(config_path=config_path)
    provision = getattr(factory, "provision_configured_keys", None)
    check = getattr(factory, "check_configured_keys", None)
    assert callable(provision), "AgentFactory must expose explicit roster provisioning"
    assert callable(check), "AgentFactory must expose a load-only roster preflight"

    first = provision()
    first_bytes = {
        path.name: path.read_bytes()
        for path in keys_dir.iterdir()
        if path.suffix in {".key", ".pub"}
    }
    second = provision()

    assert tuple(first) == handles
    assert second == first
    assert check() == first
    assert {
        path.name: path.read_bytes()
        for path in keys_dir.iterdir()
        if path.suffix in {".key", ".pub"}
    } == first_bytes


@pytest.mark.unit
def test_factory_check_reports_absent_pair_without_creating_key_directory(tmp_path: Path) -> None:
    config_path, keys_dir, _ = _write_config(tmp_path)
    factory = AgentFactory(config_path=config_path)
    check = getattr(factory, "check_configured_keys", None)
    assert callable(check), "AgentFactory must expose a load-only roster preflight"

    with pytest.raises(KeyMaterialError) as error:
        check()

    assert error.value.reason == "absent_pair"
    assert not keys_dir.exists()

"""Read-only fingerprint inspection for administrative key preflight."""

from __future__ import annotations

import hashlib
import stat
from typing import TYPE_CHECKING

import pytest
import yaml
from cryptography.hazmat.primitives import serialization

from service_auth import signing
from service_auth.factory import AgentFactory
from service_auth.signing import KeyMaterialError, generate_keypair

if TYPE_CHECKING:
    from pathlib import Path


def _expected_fingerprint(keys_dir: Path, handle: str) -> str:
    public_key = serialization.load_pem_public_key((keys_dir / f"{handle}.pub").read_bytes())
    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.unit
def test_keypair_fingerprint_validates_strict_pair_without_key_mutation(tmp_path: Path) -> None:
    generate_keypair("alice", tmp_path)
    before = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in tmp_path.iterdir()
    }

    fingerprint = signing.keypair_fingerprint("alice", tmp_path, allow_legacy_private_mode=False)

    assert fingerprint == _expected_fingerprint(tmp_path, "alice")
    assert {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in tmp_path.iterdir()
    } == before


@pytest.mark.unit
def test_keypair_fingerprint_explicitly_accepts_legacy_mode_without_chmod(tmp_path: Path) -> None:
    generate_keypair("alice", tmp_path)
    private_path = tmp_path / "alice.key"
    private_path.chmod(0o644)

    fingerprint = signing.keypair_fingerprint("alice", tmp_path, allow_legacy_private_mode=True)

    assert fingerprint == _expected_fingerprint(tmp_path, "alice")
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o644


@pytest.mark.unit
def test_keypair_fingerprint_strict_mode_rejects_legacy_permissions(tmp_path: Path) -> None:
    generate_keypair("alice", tmp_path)
    private_path = tmp_path / "alice.key"
    private_path.chmod(0o644)

    with pytest.raises(KeyMaterialError, match="expected 0600") as exc_info:
        signing.keypair_fingerprint("alice", tmp_path, allow_legacy_private_mode=False)

    assert exc_info.value.reason == "unsafe_mode"
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o644


@pytest.mark.unit
def test_keypair_fingerprint_absent_pair_does_not_create_directory(tmp_path: Path) -> None:
    keys_dir = tmp_path / "missing"

    with pytest.raises(KeyMaterialError) as exc_info:
        signing.keypair_fingerprint("alice", keys_dir, allow_legacy_private_mode=False)

    assert exc_info.value.reason == "absent_pair"
    assert not keys_dir.exists()


@pytest.mark.unit
def test_keypair_fingerprint_partial_pair_is_not_repaired(tmp_path: Path) -> None:
    generate_keypair("alice", tmp_path)
    public_path = tmp_path / "alice.pub"
    public_path.unlink()
    private_path = tmp_path / "alice.key"
    original = private_path.read_bytes()

    with pytest.raises(KeyMaterialError) as exc_info:
        signing.keypair_fingerprint("alice", tmp_path, allow_legacy_private_mode=True)

    assert exc_info.value.reason == "incomplete_pair"
    assert private_path.read_bytes() == original
    assert not public_path.exists()


@pytest.mark.unit
def test_keypair_fingerprint_may_establish_only_lock_metadata(tmp_path: Path) -> None:
    generate_keypair("alice", tmp_path)
    lock_path = tmp_path / "alice.lock"
    lock_path.unlink()
    private_before = (tmp_path / "alice.key").read_bytes()
    public_before = (tmp_path / "alice.pub").read_bytes()

    signing.keypair_fingerprint("alice", tmp_path, allow_legacy_private_mode=False)

    assert lock_path.is_file()
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert (tmp_path / "alice.key").read_bytes() == private_before
    assert (tmp_path / "alice.pub").read_bytes() == public_before


@pytest.mark.unit
def test_factory_exposes_sorted_public_targets_without_provisioning(tmp_path: Path) -> None:
    roster_path = tmp_path / "roster.yaml"
    roster_path.write_text(
        yaml.safe_dump(
            {
                "agents": {
                    "platform": {"name": "Platform", "type": "platform"},
                    "alice": {"name": "Alice", "type": "worker"},
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
                "data": {"keys_dir": "keys", "roster_path": "roster.yaml"},
                "signing": {
                    "token_ttl_seconds": 300,
                    "max_lifetime_seconds": 300,
                    "clock_skew_seconds": 30,
                },
            }
        )
    )

    targets = AgentFactory(config_path=config_path).configured_key_targets()

    assert targets == {
        "alice": tmp_path / "keys" / "alice.pub",
        "platform": tmp_path / "keys" / "platform.pub",
    }
    assert not (tmp_path / "keys").exists()

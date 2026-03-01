"""Unit tests for Ed25519 signing utilities."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import pytest

from base_agent.signing import (
    create_jws,
    generate_keypair,
    load_private_key,
    load_public_key,
    public_key_to_b64,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
class TestGenerateKeypair:
    """Tests for generate_keypair."""

    def test_creates_key_files(self, tmp_keys_dir: Path) -> None:
        _private_key, _public_key = generate_keypair("alice", tmp_keys_dir)
        assert (tmp_keys_dir / "alice.key").exists()
        assert (tmp_keys_dir / "alice.pub").exists()

    def test_keys_are_loadable(self, tmp_keys_dir: Path) -> None:
        generate_keypair("bob", tmp_keys_dir)
        private_key = load_private_key(tmp_keys_dir / "bob.key")
        public_key = load_public_key(tmp_keys_dir / "bob.pub")
        assert private_key is not None
        assert public_key is not None

    def test_creates_directory_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "keys"
        generate_keypair("carol", nested)
        assert (nested / "carol.key").exists()


@pytest.mark.unit
class TestLoadKeys:
    """Tests for load_private_key and load_public_key."""

    def test_load_missing_private_key_raises(self, tmp_keys_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_private_key(tmp_keys_dir / "nonexistent.key")

    def test_load_missing_public_key_raises(self, tmp_keys_dir: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_public_key(tmp_keys_dir / "nonexistent.pub")

    def test_load_invalid_private_key_raises(self, tmp_keys_dir: Path) -> None:
        bad_file = tmp_keys_dir / "bad.key"
        bad_file.write_text("not a key")
        with pytest.raises((ValueError, Exception)):
            load_private_key(bad_file)

    def test_roundtrip(self, tmp_keys_dir: Path) -> None:
        _original_private, original_public = generate_keypair("roundtrip", tmp_keys_dir)
        load_private_key(tmp_keys_dir / "roundtrip.key")
        loaded_public = load_public_key(tmp_keys_dir / "roundtrip.pub")
        assert public_key_to_b64(original_public) == public_key_to_b64(loaded_public)


@pytest.mark.unit
class TestPublicKeyToB64:
    """Tests for public_key_to_b64."""

    def test_returns_base64_string(self, tmp_keys_dir: Path) -> None:
        _, public_key = generate_keypair("b64test", tmp_keys_dir)
        result = public_key_to_b64(public_key)
        raw_bytes = base64.b64decode(result)
        assert len(raw_bytes) == 32

    def test_deterministic(self, tmp_keys_dir: Path) -> None:
        _, public_key = generate_keypair("det", tmp_keys_dir)
        assert public_key_to_b64(public_key) == public_key_to_b64(public_key)


@pytest.mark.unit
class TestCreateJws:
    """Tests for create_jws."""

    def test_produces_three_part_token(self, tmp_keys_dir: Path) -> None:
        private_key, _ = generate_keypair("jws", tmp_keys_dir)
        token = create_jws({"action": "test"}, private_key)
        parts = token.split(".")
        assert len(parts) == 3

    def test_header_contains_eddsa(self, tmp_keys_dir: Path) -> None:
        private_key, _ = generate_keypair("jwshdr", tmp_keys_dir)
        token = create_jws({"action": "test"}, private_key)
        header_b64 = token.split(".")[0]
        padding = "=" * (4 - len(header_b64) % 4)
        header = json.loads(base64.urlsafe_b64decode(header_b64 + padding))
        assert header["alg"] == "EdDSA"

    def test_payload_is_encoded(self, tmp_keys_dir: Path) -> None:
        private_key, _ = generate_keypair("jwspld", tmp_keys_dir)
        payload = {"action": "register", "agent_id": "a-123"}
        token = create_jws(payload, private_key)
        payload_b64 = token.split(".")[1]
        padding = "=" * (4 - len(payload_b64) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        assert decoded == payload

    def test_signature_verifies(self, tmp_keys_dir: Path) -> None:
        private_key, public_key = generate_keypair("jwsver", tmp_keys_dir)
        token = create_jws({"action": "verify"}, private_key)
        parts = token.split(".")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        sig_padding = "=" * (4 - len(parts[2]) % 4)
        signature = base64.urlsafe_b64decode(parts[2] + sig_padding)
        public_key.verify(signature, signing_input)

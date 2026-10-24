"""Fail-first proof for MP-06 key-lock pathname replacement hardening."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from service_auth import signing
from service_auth.signing import KeyMaterialError, generate_keypair, load_keypair


HANDLE = "mp06-disposable"


def _raw_public(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


@pytest.mark.unit
def test_load_keypair_rejects_lock_path_replacement_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An opened lock inode must still be the inode named by the canonical path."""
    _private_key, expected_public = generate_keypair(HANDLE, tmp_path)
    lock_path = tmp_path / f"{HANDLE}.lock"
    original_identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
    real_flock = signing.fcntl.flock
    replacement_identity: tuple[int, int] | None = None

    def replace_path_before_flock(file_descriptor: int, operation: int) -> None:
        nonlocal replacement_identity
        if operation == signing.fcntl.LOCK_SH and replacement_identity is None:
            opened = os.fstat(file_descriptor)
            assert (opened.st_dev, opened.st_ino) == original_identity
            lock_path.unlink()
            replacement_descriptor = os.open(
                lock_path,
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(replacement_descriptor)
            replacement = lock_path.lstat()
            replacement_identity = (replacement.st_dev, replacement.st_ino)
            assert replacement_identity != original_identity
        real_flock(file_descriptor, operation)

    monkeypatch.setattr(signing.fcntl, "flock", replace_path_before_flock)

    with pytest.raises(KeyMaterialError) as caught:
        _private_key, loaded_public = load_keypair(HANDLE, tmp_path)
        assert _raw_public(loaded_public) == _raw_public(expected_public)

    assert replacement_identity is not None
    assert caught.value.reason == "unsafe_path"
    assert "lock" in str(caught.value).lower()


@pytest.mark.unit
def test_load_keypair_control_loads_an_ordinary_generated_pair(tmp_path: Path) -> None:
    _private_key, expected_public = generate_keypair(HANDLE, tmp_path)

    _loaded_private, loaded_public = load_keypair(HANDLE, tmp_path)

    assert _raw_public(loaded_public) == _raw_public(expected_public)


@pytest.mark.unit
def test_load_keypair_exception_releases_the_shared_lock(tmp_path: Path) -> None:
    generate_keypair(HANDLE, tmp_path)
    private_path = tmp_path / f"{HANDLE}.key"
    lock_path = tmp_path / f"{HANDLE}.lock"
    private_path.write_bytes(b"not a private key")
    private_path.chmod(0o600)

    with pytest.raises(KeyMaterialError) as caught:
        load_keypair(HANDLE, tmp_path)

    assert caught.value.reason == "corrupt_private"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)

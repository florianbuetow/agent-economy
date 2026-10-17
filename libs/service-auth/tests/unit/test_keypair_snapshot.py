"""Acceptance tests for atomic MP-06 keypair snapshots."""

from __future__ import annotations

import fcntl
import hashlib
import multiprocessing
import os
import stat
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from cryptography.hazmat.primitives import serialization

from service_auth import signing
from service_auth.signing import KeyMaterialError, generate_keypair, load_keypair

if TYPE_CHECKING:
    from collections.abc import Callable
    from multiprocessing.synchronize import Event


def _snapshot_api() -> Callable[..., tuple[Any, ...]]:
    snapshotter = getattr(signing, "snapshot_keypairs", None)
    assert callable(snapshotter), "MP-06 requires the public snapshot_keypairs batch API"
    return cast("Callable[..., tuple[Any, ...]]", snapshotter)


def _public_path(keys_dir: Path, handle: str) -> Path:
    return keys_dir / f"{handle}.pub"


def _hold_exclusive_lock(
    lock_path: str,
    ready: Event,
    release: Event,
) -> None:
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        ready.set()
        if not release.wait(timeout=10):
            raise TimeoutError("parent did not release key lock holder")
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _repair_private_mode_under_exclusive_lock(
    lock_path: str,
    private_path: str,
    ready: Event,
    release: Event,
) -> None:
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        Path(private_path).chmod(0o644)
        ready.set()
        if not release.wait(timeout=10):
            raise TimeoutError("parent did not release key transition")
        Path(private_path).chmod(0o600)
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


@pytest.mark.unit
def test_snapshot_keypairs_preserves_raw_bytes_modes_and_fingerprints(tmp_path: Path) -> None:
    generate_keypair("bravo", tmp_path)
    generate_keypair("alpha", tmp_path)
    targets = (
        ("bravo", _public_path(tmp_path, "bravo")),
        ("alpha", _public_path(tmp_path, "alpha")),
    )

    snapshots = _snapshot_api()(targets, blocking=False)

    assert [snapshot.handle for snapshot in snapshots] == ["alpha", "bravo"]
    for snapshot in snapshots:
        private_path = tmp_path / f"{snapshot.handle}.key"
        public_path = tmp_path / f"{snapshot.handle}.pub"
        assert snapshot.private_pem == private_path.read_bytes()
        assert snapshot.public_pem == public_path.read_bytes()
        assert snapshot.private_mode == stat.S_IMODE(private_path.stat().st_mode)
        assert snapshot.public_mode == stat.S_IMODE(public_path.stat().st_mode)
        public_key = serialization.load_pem_public_key(snapshot.public_pem)
        raw_public = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        assert snapshot.public_fingerprint_sha256 == hashlib.sha256(raw_public).hexdigest()
        assert snapshot.private_pem not in repr(snapshot).encode()
        with pytest.raises((AttributeError, TypeError)):
            snapshot.handle = "changed"


@pytest.mark.unit
@pytest.mark.parametrize("blocking", [None, 0, 1, "false"])
def test_snapshot_keypairs_requires_an_explicit_boolean_blocking_mode(
    tmp_path: Path,
    blocking: object,
) -> None:
    generate_keypair("alpha", tmp_path)

    with pytest.raises((TypeError, ValueError), match="blocking"):
        _snapshot_api()((("alpha", _public_path(tmp_path, "alpha")),), blocking=blocking)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("handle", "filename"),
    [
        ("", ".pub"),
        ("../alpha", "alpha.pub"),
        ("alpha", "renamed.pub"),
        ("alpha", "alpha.key"),
    ],
)
def test_snapshot_keypairs_rejects_noncanonical_targets_before_lock_creation(
    tmp_path: Path,
    handle: str,
    filename: str,
) -> None:
    generate_keypair("alpha", tmp_path)
    (tmp_path / "alpha.lock").unlink()

    with pytest.raises((KeyMaterialError, ValueError)):
        _snapshot_api()(((handle, tmp_path / filename),), blocking=False)

    assert not (tmp_path / "alpha.lock").exists()


@pytest.mark.unit
def test_snapshot_keypairs_rejects_duplicate_paths_before_lock_creation(tmp_path: Path) -> None:
    generate_keypair("alpha", tmp_path)
    (tmp_path / "alpha.lock").unlink()
    target = ("alpha", _public_path(tmp_path, "alpha"))

    with pytest.raises(KeyMaterialError) as caught:
        _snapshot_api()((target, target), blocking=False)

    assert caught.value.reason == "path_collision"
    assert not (tmp_path / "alpha.lock").exists()


@pytest.mark.unit
def test_snapshot_keypairs_establishes_only_missing_legacy_lock_metadata(tmp_path: Path) -> None:
    generate_keypair("alpha", tmp_path)
    private_path = tmp_path / "alpha.key"
    public_path = tmp_path / "alpha.pub"
    lock_path = tmp_path / "alpha.lock"
    lock_path.unlink()
    pair_before = (private_path.read_bytes(), public_path.read_bytes())

    snapshots = _snapshot_api()((("alpha", public_path),), blocking=False)

    assert [snapshot.handle for snapshot in snapshots] == ["alpha"]
    assert (private_path.read_bytes(), public_path.read_bytes()) == pair_before
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_snapshot_keypairs_absent_pair_may_create_only_coordination_lock(tmp_path: Path) -> None:
    public_path = tmp_path / "alpha.pub"

    with pytest.raises(KeyMaterialError) as caught:
        _snapshot_api()((("alpha", public_path),), blocking=False)

    assert caught.value.reason == "absent_pair"
    assert {path.name for path in tmp_path.iterdir()} == {"alpha.lock"}
    assert stat.S_IMODE((tmp_path / "alpha.lock").stat().st_mode) == 0o600


@pytest.mark.unit
@pytest.mark.parametrize("damage", ["missing", "partial", "private-mode", "mismatch"])
def test_snapshot_keypairs_rejects_invalid_pairs_without_mutating_bytes(
    tmp_path: Path,
    damage: str,
) -> None:
    pair_a = tmp_path / "pair-a"
    pair_b = tmp_path / "pair-b"
    generate_keypair("alpha", pair_a)
    generate_keypair("alpha", pair_b)
    private_path = pair_a / "alpha.key"
    public_path = pair_a / "alpha.pub"
    lock_path = pair_a / "alpha.lock"
    lock_path.unlink()
    if damage == "missing":
        private_path.unlink()
        public_path.unlink()
    elif damage == "partial":
        public_path.unlink()
    elif damage == "private-mode":
        private_path.chmod(0o644)
    else:
        public_path.write_bytes((pair_b / "alpha.pub").read_bytes())
    before = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in (private_path, public_path)
        if path.exists()
    }

    with pytest.raises(KeyMaterialError):
        _snapshot_api()((("alpha", public_path),), blocking=False)

    after = {
        path.name: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in (private_path, public_path)
        if path.exists()
    }
    assert after == before


@pytest.mark.unit
def test_snapshot_keypairs_rejects_symlinked_parent_before_lock_creation(tmp_path: Path) -> None:
    real_keys = tmp_path / "real-keys"
    generate_keypair("alpha", real_keys)
    (real_keys / "alpha.lock").unlink()
    linked_keys = tmp_path / "linked-keys"
    linked_keys.symlink_to(real_keys, target_is_directory=True)

    with pytest.raises(KeyMaterialError) as caught:
        _snapshot_api()((("alpha", linked_keys / "alpha.pub"),), blocking=False)

    assert caught.value.reason == "unsafe_path"
    assert not (real_keys / "alpha.lock").exists()


@pytest.mark.unit
def test_snapshot_keypairs_nonblocking_fails_under_process_contention(tmp_path: Path) -> None:
    generate_keypair("alpha", tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_exclusive_lock,
        args=(str(tmp_path / "alpha.lock"), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(timeout=10), "exclusive holder did not acquire the lock"
        with pytest.raises(KeyMaterialError) as caught:
            _snapshot_api()((("alpha", _public_path(tmp_path, "alpha")),), blocking=False)
        assert caught.value.reason == "lock_unavailable"
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
    assert holder.exitcode == 0


@pytest.mark.unit
def test_snapshot_blocking_waits_for_exclusive_transition_before_validation(tmp_path: Path) -> None:
    generate_keypair("alpha", tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    transition = context.Process(
        target=_repair_private_mode_under_exclusive_lock,
        args=(
            str(tmp_path / "alpha.lock"),
            str(tmp_path / "alpha.key"),
            ready,
            release,
        ),
    )
    transition.start()
    timer = threading.Timer(0.2, release.set)
    try:
        assert ready.wait(timeout=10), "exclusive transition did not start"
        timer.start()
        snapshots = _snapshot_api()(
            (("alpha", _public_path(tmp_path, "alpha")),),
            blocking=True,
        )
        assert [snapshot.handle for snapshot in snapshots] == ["alpha"]
    finally:
        release.set()
        timer.cancel()
        transition.join(timeout=10)
        if transition.is_alive():
            transition.terminate()
            transition.join(timeout=5)
    assert transition.exitcode == 0


@pytest.mark.unit
def test_snapshot_nonblocking_reports_contention_before_mutable_pair_state(tmp_path: Path) -> None:
    generate_keypair("alpha", tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    transition = context.Process(
        target=_repair_private_mode_under_exclusive_lock,
        args=(
            str(tmp_path / "alpha.lock"),
            str(tmp_path / "alpha.key"),
            ready,
            release,
        ),
    )
    transition.start()
    try:
        assert ready.wait(timeout=10), "exclusive transition did not start"
        with pytest.raises(KeyMaterialError) as caught:
            _snapshot_api()(
                (("alpha", _public_path(tmp_path, "alpha")),),
                blocking=False,
            )
        assert caught.value.reason == "lock_unavailable"
    finally:
        release.set()
        transition.join(timeout=10)
        if transition.is_alive():
            transition.terminate()
            transition.join(timeout=5)
    assert transition.exitcode == 0


@pytest.mark.unit
def test_snapshot_rechecks_inode_collisions_under_the_complete_lock_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_keypair("alpha", tmp_path)
    generate_keypair("bravo", tmp_path)
    real_prepare = signing._prepare_snapshot_targets

    def collide_after_preflight(targets: tuple[tuple[str, Path], ...]) -> tuple[Any, ...]:
        prepared = real_prepare(targets)
        bravo_public = tmp_path / "bravo.pub"
        bravo_public.unlink()
        os.link(tmp_path / "alpha.pub", bravo_public)
        return prepared

    monkeypatch.setattr(signing, "_prepare_snapshot_targets", collide_after_preflight)

    with pytest.raises(KeyMaterialError) as caught:
        _snapshot_api()(
            (
                ("alpha", _public_path(tmp_path, "alpha")),
                ("bravo", _public_path(tmp_path, "bravo")),
            ),
            blocking=False,
        )

    assert caught.value.reason == "path_collision"


@pytest.mark.unit
def test_later_lock_contention_releases_every_earlier_snapshot_lock(tmp_path: Path) -> None:
    generate_keypair("alpha", tmp_path)
    generate_keypair("bravo", tmp_path)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_exclusive_lock,
        args=(str(tmp_path / "bravo.lock"), ready, release),
    )
    holder.start()
    try:
        assert ready.wait(timeout=10), "later exclusive holder did not acquire the lock"
        with pytest.raises(KeyMaterialError) as caught:
            _snapshot_api()(
                (
                    ("alpha", _public_path(tmp_path, "alpha")),
                    ("bravo", _public_path(tmp_path, "bravo")),
                ),
                blocking=False,
            )
        assert caught.value.reason == "lock_unavailable"
        descriptor = os.open(
            tmp_path / "alpha.lock",
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    finally:
        release.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=5)
    assert holder.exitcode == 0


@pytest.mark.unit
def test_snapshot_keypairs_holds_every_lock_until_all_pairs_are_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_keypair("alpha", tmp_path)
    generate_keypair("bravo", tmp_path)
    real_parse_private = signing._parse_private_key
    checked = False

    def verify_all_locks_held(key_bytes: bytes, path: Path) -> Any:
        nonlocal checked
        if path.name == "bravo.key":
            descriptors = [
                os.open(
                    tmp_path / f"{handle}.lock",
                    os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
                )
                for handle in ("alpha", "bravo")
            ]
            try:
                for descriptor in descriptors:
                    with pytest.raises(BlockingIOError):
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                checked = True
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)
        return real_parse_private(key_bytes, path)

    monkeypatch.setattr(signing, "_parse_private_key", verify_all_locks_held)

    _snapshot_api()(
        (
            ("bravo", _public_path(tmp_path, "bravo")),
            ("alpha", _public_path(tmp_path, "alpha")),
        ),
        blocking=False,
    )

    assert checked


@pytest.mark.unit
def test_snapshot_keypairs_exception_releases_all_acquired_locks(tmp_path: Path) -> None:
    generate_keypair("alpha", tmp_path)
    generate_keypair("bravo", tmp_path)
    (tmp_path / "bravo.key").write_bytes(b"not a private key")

    with pytest.raises(KeyMaterialError) as caught:
        _snapshot_api()(
            (
                ("alpha", _public_path(tmp_path, "alpha")),
                ("bravo", _public_path(tmp_path, "bravo")),
            ),
            blocking=False,
        )

    assert caught.value.reason == "corrupt_private"
    for handle in ("alpha", "bravo"):
        descriptor = os.open(
            tmp_path / f"{handle}.lock",
            os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@pytest.mark.unit
def test_snapshot_keypairs_rejects_lock_path_replacement_after_flock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_keypair("alpha", tmp_path)
    lock_path = tmp_path / "alpha.lock"
    original_identity = (lock_path.stat().st_dev, lock_path.stat().st_ino)
    real_flock = signing.fcntl.flock
    replaced = False

    def replace_named_lock(file_descriptor: int, operation: int) -> None:
        nonlocal replaced
        real_flock(file_descriptor, operation)
        if operation & signing.fcntl.LOCK_SH and not replaced:
            opened = os.fstat(file_descriptor)
            assert (opened.st_dev, opened.st_ino) == original_identity
            lock_path.unlink()
            replacement = os.open(
                lock_path,
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(replacement)
            replaced = True

    monkeypatch.setattr(signing.fcntl, "flock", replace_named_lock)

    with pytest.raises(KeyMaterialError) as caught:
        _snapshot_api()((("alpha", _public_path(tmp_path, "alpha")),), blocking=False)

    assert replaced
    assert caught.value.reason == "unsafe_path"


@pytest.mark.unit
def test_public_load_rejects_lock_path_replacement_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generate_keypair("alpha", tmp_path)
    lock_path = tmp_path / "alpha.lock"
    real_flock = signing.fcntl.flock
    replaced = False

    def replace_path_before_flock(file_descriptor: int, operation: int) -> None:
        nonlocal replaced
        if operation == signing.fcntl.LOCK_SH and not replaced:
            lock_path.unlink()
            replacement = os.open(
                lock_path,
                os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            os.close(replacement)
            replaced = True
        real_flock(file_descriptor, operation)

    monkeypatch.setattr(signing.fcntl, "flock", replace_path_before_flock)

    with pytest.raises(KeyMaterialError) as caught:
        load_keypair("alpha", tmp_path)

    assert replaced
    assert caught.value.reason == "unsafe_path"

"""
Ed25519 key management, JWS token creation, and verification.

Handles key generation, loading, and compact JWS (header.payload.signature)
token creation and verification using EdDSA for authenticating with platform services.

The payload is serialized with a single canonicalization
(``json.dumps(payload, sort_keys=True, separators=(",", ":"))``) so every signer in
the system produces byte-identical payloads. Verification is performed over the
bytes exactly as sent, so tokens produced by older signers with a different
canonicalization remain verifiable during rollout.
"""

from __future__ import annotations

import base64
import errno
import fcntl
import hashlib
import os
import stat
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from service_auth.jws import (
    TokenExpiredError,
    TokenPolicy,
    VerifiedJws,
    create_jws,
    verify_jws,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


__all__ = [
    "KeyMaterialError",
    "KeypairSnapshot",
    "PlatformSigner",
    "TokenExpiredError",
    "TokenPolicy",
    "VerifiedJws",
    "adopt_keypair_permissions",
    "create_jws",
    "generate_keypair",
    "keypair_fingerprint",
    "load_keypair",
    "load_private_key",
    "load_public_key",
    "public_key_to_b64",
    "snapshot_keypairs",
    "verify_jws",
]


class KeyMaterialError(ValueError):
    """Raised when durable key material violates the key-store contract."""

    def __init__(self, reason: str, message: str) -> None:
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class KeypairSnapshot:
    """Immutable validated copy of one durable Ed25519 keypair."""

    handle: str
    private_pem: bytes = field(repr=False)
    public_pem: bytes
    public_fingerprint_sha256: str
    private_mode: int
    public_mode: int


@dataclass(frozen=True, slots=True)
class _SnapshotTarget:
    handle: str
    private_path: Path
    public_path: Path
    lock_path: Path


def _validate_handle(handle: str) -> None:
    if not handle or handle in {".", ".."} or "/" in handle or "\\" in handle:
        msg = f"Invalid unsafe key handle: {handle!r}"
        raise KeyMaterialError("unsafe_handle", msg)


def _validate_keys_dir(keys_dir: Path, *, create: bool) -> None:
    if create:
        keys_dir.mkdir(parents=True, exist_ok=True)
    try:
        directory_stat = keys_dir.lstat()
    except FileNotFoundError as exc:
        msg = f"Keypair is not provisioned: key directory does not exist: {keys_dir}"
        raise KeyMaterialError("absent_pair", msg) from exc
    if stat.S_ISLNK(directory_stat.st_mode) or not stat.S_ISDIR(directory_stat.st_mode):
        msg = f"Unsafe key directory must be a real directory, not a symlink: {keys_dir}"
        raise KeyMaterialError("unsafe_path", msg)


def _key_paths(handle: str, keys_dir: Path) -> tuple[Path, Path, Path]:
    return (
        keys_dir / f"{handle}.key",
        keys_dir / f"{handle}.pub",
        keys_dir / f"{handle}.lock",
    )


def _path_kind(path: Path) -> str:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return "missing"
    if stat.S_ISLNK(path_stat.st_mode):
        return "symlink"
    if stat.S_ISREG(path_stat.st_mode):
        return "regular"
    return "other"


def _open_lock_file(lock_path: Path, *, create: bool) -> int:
    lock_kind = _path_kind(lock_path)
    if lock_kind in {"symlink", "other"}:
        msg = f"Unsafe key lock must be a regular file, not a symlink: {lock_path}"
        raise KeyMaterialError("unsafe_path", msg)
    if lock_kind == "missing" and not create:
        msg = f"Keypair is not provisioned: lock file does not exist: {lock_path}"
        raise KeyMaterialError("absent_pair", msg)

    flags = os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW
    created = False
    if lock_kind == "missing":
        try:
            file_descriptor = os.open(lock_path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            file_descriptor = os.open(lock_path, flags)
    else:
        file_descriptor = os.open(lock_path, flags)

    try:
        lock_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(lock_stat.st_mode):
            msg = f"Unsafe key lock must be a regular file: {lock_path}"
            raise KeyMaterialError("unsafe_path", msg)
        if created:
            os.fchmod(file_descriptor, 0o600)
            lock_stat = os.fstat(file_descriptor)
        if stat.S_IMODE(lock_stat.st_mode) != 0o600:
            msg = f"Unsafe key lock mode for {lock_path}: expected 0600"
            raise KeyMaterialError("unsafe_mode", msg)
        _validate_lock_identity(file_descriptor, lock_path, stage="opening")
        return file_descriptor
    except BaseException:
        os.close(file_descriptor)
        raise


def _validate_lock_identity(file_descriptor: int, lock_path: Path, *, stage: str) -> None:
    opened_stat = os.fstat(file_descriptor)
    try:
        current_stat = lock_path.lstat()
    except FileNotFoundError as exc:
        msg = f"Unsafe key lock path disappeared while {stage}: {lock_path}"
        raise KeyMaterialError("unsafe_path", msg) from exc
    if (
        not stat.S_ISREG(opened_stat.st_mode)
        or not stat.S_ISREG(current_stat.st_mode)
        or opened_stat.st_dev != current_stat.st_dev
        or opened_stat.st_ino != current_stat.st_ino
    ):
        msg = f"Unsafe key lock path changed while {stage}: {lock_path}"
        raise KeyMaterialError("unsafe_path", msg)


@contextmanager
def _key_lock(
    lock_path: Path,
    *,
    exclusive: bool,
    create: bool,
    blocking: bool,
) -> Iterator[int]:
    try:
        file_descriptor = _open_lock_file(lock_path, create=create)
    except OSError as exc:
        msg = f"Unsafe key lock path cannot be opened: {lock_path}"
        raise KeyMaterialError("unsafe_path", msg) from exc
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        operation |= fcntl.LOCK_NB
    try:
        try:
            fcntl.flock(file_descriptor, operation)
        except OSError as exc:
            if not blocking and exc.errno in {errno.EACCES, errno.EAGAIN}:
                msg = f"Key lock is already held: {lock_path}"
                raise KeyMaterialError("lock_unavailable", msg) from exc
            raise
        try:
            _validate_lock_identity(file_descriptor, lock_path, stage="acquiring flock")
            yield file_descriptor
        except BaseException as primary_error:
            try:
                fcntl.flock(file_descriptor, fcntl.LOCK_UN)
            except BaseException as unlock_error:
                primary_error.add_note(f"Key lock release also failed: {unlock_error}")
            raise
        else:
            fcntl.flock(file_descriptor, fcntl.LOCK_UN)
    finally:
        os.close(file_descriptor)


def _read_regular_file_state(path: Path, *, private: bool) -> tuple[bytes, int]:
    path_kind = _path_kind(path)
    if path_kind != "regular":
        msg = f"Unsafe key path must be a regular file, not a symlink: {path}"
        raise KeyMaterialError("unsafe_path", msg)
    try:
        file_descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except OSError as exc:
        msg = f"Unsafe key path cannot be opened without following symlinks: {path}"
        raise KeyMaterialError("unsafe_path", msg) from exc
    try:
        opened_stat = os.fstat(file_descriptor)
        current_stat = path.lstat()
        if (
            not stat.S_ISREG(opened_stat.st_mode)
            or opened_stat.st_dev != current_stat.st_dev
            or opened_stat.st_ino != current_stat.st_ino
        ):
            msg = f"Unsafe key path changed while opening: {path}"
            raise KeyMaterialError("unsafe_path", msg)
        if private and stat.S_IMODE(opened_stat.st_mode) != 0o600:
            mode = stat.S_IMODE(opened_stat.st_mode)
            msg = f"Unsafe private key mode for {path}: expected 0600, got {mode:04o}"
            raise KeyMaterialError("unsafe_mode", msg)
        mode = stat.S_IMODE(opened_stat.st_mode)
        with os.fdopen(file_descriptor, "rb") as key_file:
            file_descriptor = -1
            key_bytes = key_file.read()
            final_stat = os.fstat(key_file.fileno())
            final_current_stat = path.lstat()
            if (
                not stat.S_ISREG(final_stat.st_mode)
                or final_stat.st_dev != opened_stat.st_dev
                or final_stat.st_ino != opened_stat.st_ino
                or final_current_stat.st_dev != opened_stat.st_dev
                or final_current_stat.st_ino != opened_stat.st_ino
            ):
                msg = f"Unsafe key path changed while reading: {path}"
                raise KeyMaterialError("unsafe_path", msg)
            return key_bytes, mode
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)


def _read_regular_file(path: Path, *, private: bool) -> bytes:
    key_bytes, _mode = _read_regular_file_state(path, private=private)
    return key_bytes


def _parse_private_key(key_bytes: bytes, path: Path) -> Ed25519PrivateKey:
    try:
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
    except (TypeError, ValueError) as exc:
        msg = f"Invalid or corrupt private key PEM: {path}"
        raise KeyMaterialError("corrupt_private", msg) from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        msg = f"Invalid private key type for {path}: expected Ed25519"
        raise KeyMaterialError("corrupt_private", msg)
    return private_key


def _parse_public_key(key_bytes: bytes, path: Path) -> Ed25519PublicKey:
    try:
        public_key = serialization.load_pem_public_key(key_bytes)
    except (TypeError, ValueError) as exc:
        msg = f"Invalid or corrupt public key PEM: {path}"
        raise KeyMaterialError("corrupt_public", msg) from exc
    if not isinstance(public_key, Ed25519PublicKey):
        msg = f"Invalid public key type for {path}: expected Ed25519"
        raise KeyMaterialError("corrupt_public", msg)
    return public_key


def _read_keypair(
    private_path: Path,
    public_path: Path,
    *,
    require_private_mode: bool,
) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    private_kind = _path_kind(private_path)
    public_kind = _path_kind(public_path)
    if private_kind == "missing" and public_kind == "missing":
        msg = f"Keypair is not provisioned: missing {private_path} and {public_path}"
        raise KeyMaterialError("absent_pair", msg)
    if private_kind == "missing" or public_kind == "missing":
        msg = f"Incomplete keypair: private={private_kind}, public={public_kind}"
        raise KeyMaterialError("incomplete_pair", msg)
    private_key = _parse_private_key(
        _read_regular_file(private_path, private=require_private_mode),
        private_path,
    )
    public_key = _parse_public_key(
        _read_regular_file(public_path, private=False),
        public_path,
    )
    derived_public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    stored_public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    if derived_public != stored_public:
        msg = f"Keypair mismatch: private key does not match public key for {private_path.stem}"
        raise KeyMaterialError("mismatched_pair", msg)
    return private_key, public_key


def _write_staged_file(keys_dir: Path, prefix: str, data: bytes, mode: int) -> Path:
    file_descriptor, raw_path = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=keys_dir)
    staged_path = Path(raw_path)
    try:
        os.fchmod(file_descriptor, mode)
        with os.fdopen(file_descriptor, "wb") as staged_file:
            file_descriptor = -1
            staged_file.write(data)
            staged_file.flush()
            os.fsync(staged_file.fileno())
        return staged_path
    except BaseException:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        staged_path.unlink(missing_ok=True)
        raise


def _fsync_directory(keys_dir: Path) -> None:
    directory_descriptor = os.open(keys_dir, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def generate_keypair(handle: str, keys_dir: Path) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Provision or load one durable Ed25519 pair without replacing key material."""
    _validate_handle(handle)
    _validate_keys_dir(keys_dir, create=True)
    private_path, public_path, lock_path = _key_paths(handle, keys_dir)

    with _key_lock(lock_path, exclusive=True, create=True, blocking=True):
        private_kind = _path_kind(private_path)
        public_kind = _path_kind(public_path)
        if private_kind != "missing" or public_kind != "missing":
            return _read_keypair(private_path, public_path, require_private_mode=True)

        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        private_bytes = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        private_staged: Path | None = None
        public_staged: Path | None = None
        try:
            private_staged = _write_staged_file(
                keys_dir,
                f".{handle}.private-",
                private_bytes,
                0o600,
            )
            public_staged = _write_staged_file(
                keys_dir,
                f".{handle}.public-",
                public_bytes,
                0o644,
            )
            os.link(public_staged, public_path)
            _fsync_directory(keys_dir)
            os.link(private_staged, private_path)
            _fsync_directory(keys_dir)
        except FileExistsError as exc:
            msg = f"Keypair publication conflict for handle {handle!r}; no key was replaced"
            raise KeyMaterialError("publication_conflict", msg) from exc
        finally:
            if private_staged is not None:
                private_staged.unlink(missing_ok=True)
            if public_staged is not None:
                public_staged.unlink(missing_ok=True)

        return _read_keypair(private_path, public_path, require_private_mode=True)


def load_keypair(handle: str, keys_dir: Path) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Load and validate one provisioned Ed25519 pair under its shared lock."""
    _validate_handle(handle)
    _validate_keys_dir(keys_dir, create=False)
    private_path, public_path, lock_path = _key_paths(handle, keys_dir)
    lock_kind = _path_kind(lock_path)
    private_kind = _path_kind(private_path)
    public_kind = _path_kind(public_path)
    create_lock = lock_kind == "missing" and (private_kind != "missing" or public_kind != "missing")
    if lock_kind == "missing" and not create_lock:
        msg = f"Keypair is not provisioned: missing {private_path} and {public_path}"
        raise KeyMaterialError("absent_pair", msg)
    with _key_lock(lock_path, exclusive=False, create=create_lock, blocking=True):
        return _read_keypair(private_path, public_path, require_private_mode=True)


def _validate_real_directory_components(directory: Path) -> None:
    for component in reversed((directory, *directory.parents)):
        try:
            component_stat = component.lstat()
        except FileNotFoundError as exc:
            msg = f"Key directory component does not exist: {component}"
            raise KeyMaterialError("absent_pair", msg) from exc
        if stat.S_ISLNK(component_stat.st_mode) or not stat.S_ISDIR(component_stat.st_mode):
            msg = f"Unsafe key directory component must be a real directory: {component}"
            raise KeyMaterialError("unsafe_path", msg)


def _prepare_snapshot_targets(
    targets: tuple[tuple[str, Path], ...],
) -> tuple[_SnapshotTarget, ...]:
    prepared: list[_SnapshotTarget] = []
    paths: dict[Path, str] = {}
    for handle, supplied_public_path in targets:
        _validate_handle(handle)
        if not isinstance(supplied_public_path, Path):
            msg = "Snapshot public-key paths must be pathlib.Path values"
            raise TypeError(msg)
        public_path = supplied_public_path
        if (
            not public_path.is_absolute()
            or ".." in public_path.parts
            or public_path.name != f"{handle}.pub"
        ):
            msg = (
                "Snapshot targets require an absolute normalized canonical "
                f"'<handle>.pub' path: {supplied_public_path}"
            )
            raise KeyMaterialError("unsafe_path", msg)
        _validate_real_directory_components(public_path.parent)
        private_path, expected_public_path, lock_path = _key_paths(handle, public_path.parent)
        if public_path != expected_public_path:
            msg = f"Noncanonical public-key path for handle {handle!r}: {public_path}"
            raise KeyMaterialError("unsafe_path", msg)
        target = _SnapshotTarget(
            handle=handle,
            private_path=private_path,
            public_path=public_path,
            lock_path=lock_path,
        )
        for role, candidate in (
            ("private", private_path),
            ("public", public_path),
            ("lock", lock_path),
        ):
            existing_role = paths.get(candidate)
            if existing_role is not None:
                msg = f"Key snapshot path collision between {existing_role} and {role}: {candidate}"
                raise KeyMaterialError("path_collision", msg)
            paths[candidate] = role
        prepared.append(target)
    return tuple(sorted(prepared, key=lambda target: os.fspath(target.lock_path)))


def _validate_snapshot_inventory(
    prepared: tuple[_SnapshotTarget, ...],
    lock_descriptors: dict[Path, int],
) -> None:
    inodes: dict[tuple[int, int], Path] = {}
    for target in prepared:
        private_kind = _path_kind(target.private_path)
        public_kind = _path_kind(target.public_path)
        if private_kind == "missing" and public_kind == "missing":
            msg = (
                f"Keypair is not provisioned: missing {target.private_path} "
                f"and {target.public_path}"
            )
            raise KeyMaterialError("absent_pair", msg)
        if private_kind == "missing" or public_kind == "missing":
            msg = f"Incomplete keypair: private={private_kind}, public={public_kind}"
            raise KeyMaterialError("incomplete_pair", msg)

        lock_descriptor = lock_descriptors[target.lock_path]
        _validate_lock_identity(
            lock_descriptor,
            target.lock_path,
            stage="validating snapshot inventory",
        )
        for candidate in (target.private_path, target.public_path, target.lock_path):
            try:
                candidate_stat = candidate.lstat()
            except FileNotFoundError as exc:
                msg = f"Key snapshot path disappeared under lock: {candidate}"
                raise KeyMaterialError("unsafe_path", msg) from exc
            if stat.S_ISLNK(candidate_stat.st_mode) or not stat.S_ISREG(candidate_stat.st_mode):
                msg = f"Unsafe key snapshot path must be a regular file: {candidate}"
                raise KeyMaterialError("unsafe_path", msg)
            identity = (candidate_stat.st_dev, candidate_stat.st_ino)
            existing_path = inodes.get(identity)
            if existing_path is not None:
                msg = f"Key snapshot paths share one file: {existing_path} and {candidate}"
                raise KeyMaterialError("path_collision", msg)
            inodes[identity] = candidate

        private_mode = stat.S_IMODE(target.private_path.lstat().st_mode)
        if private_mode != 0o600:
            msg = (
                f"Unsafe private key mode for {target.private_path}: "
                f"expected 0600, got {private_mode:04o}"
            )
            raise KeyMaterialError("unsafe_mode", msg)


def snapshot_keypairs(
    targets: tuple[tuple[str, Path], ...],
    *,
    blocking: bool,
) -> tuple[KeypairSnapshot, ...]:
    """Capture validated raw keypair bytes while holding the full lock inventory."""
    if not isinstance(blocking, bool):
        msg = "blocking must be a bool"
        raise TypeError(msg)
    prepared = _prepare_snapshot_targets(targets)
    snapshots: list[KeypairSnapshot] = []
    lock_descriptors: dict[Path, int] = {}
    with ExitStack() as lock_stack:
        for target in prepared:
            lock_descriptors[target.lock_path] = lock_stack.enter_context(
                _key_lock(
                    target.lock_path,
                    exclusive=False,
                    create=True,
                    blocking=blocking,
                )
            )
        _validate_snapshot_inventory(prepared, lock_descriptors)
        for target in prepared:
            private_pem, private_mode = _read_regular_file_state(
                target.private_path,
                private=True,
            )
            public_pem, public_mode = _read_regular_file_state(
                target.public_path,
                private=False,
            )
            private_key = _parse_private_key(private_pem, target.private_path)
            public_key = _parse_public_key(public_pem, target.public_path)
            derived_public = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            stored_public = public_key.public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            if derived_public != stored_public:
                msg = f"Keypair mismatch: private key does not match public key for {target.handle}"
                raise KeyMaterialError("mismatched_pair", msg)
            snapshots.append(
                KeypairSnapshot(
                    handle=target.handle,
                    private_pem=private_pem,
                    public_pem=public_pem,
                    public_fingerprint_sha256=hashlib.sha256(stored_public).hexdigest(),
                    private_mode=private_mode,
                    public_mode=public_mode,
                )
            )
    return tuple(snapshots)


def keypair_fingerprint(
    handle: str,
    keys_dir: Path,
    *,
    allow_legacy_private_mode: bool,
) -> str:
    """Validate one existing pair under its lock and return its SHA-256 fingerprint.

    This inspection never creates or replaces key material and never changes private-key
    permissions. It may establish the pair's persistent lock sidecar when validating legacy
    material that predates lock files.
    """
    if not isinstance(allow_legacy_private_mode, bool):
        msg = "allow_legacy_private_mode must be a bool"
        raise ValueError(msg)
    _validate_handle(handle)
    _validate_keys_dir(keys_dir, create=False)
    private_path, public_path, lock_path = _key_paths(handle, keys_dir)
    lock_kind = _path_kind(lock_path)
    private_kind = _path_kind(private_path)
    public_kind = _path_kind(public_path)
    create_lock = lock_kind == "missing" and (private_kind != "missing" or public_kind != "missing")
    if lock_kind == "missing" and not create_lock:
        msg = f"Keypair is not provisioned: missing {private_path} and {public_path}"
        raise KeyMaterialError("absent_pair", msg)
    with _key_lock(lock_path, exclusive=False, create=create_lock, blocking=True):
        _private_key, public_key = _read_keypair(
            private_path,
            public_path,
            require_private_mode=not allow_legacy_private_mode,
        )
    raw_public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw_public).hexdigest()


def adopt_keypair_permissions(
    handle: str,
    keys_dir: Path,
) -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Explicitly adopt a valid legacy pair by hardening only its private mode."""
    _validate_handle(handle)
    _validate_keys_dir(keys_dir, create=False)
    private_path, public_path, lock_path = _key_paths(handle, keys_dir)
    with _key_lock(lock_path, exclusive=True, create=True, blocking=True):
        _private_key, public_key = _read_keypair(
            private_path,
            public_path,
            require_private_mode=False,
        )
        file_descriptor = os.open(
            private_path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        try:
            opened_stat = os.fstat(file_descriptor)
            current_stat = private_path.lstat()
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != current_stat.st_dev
                or opened_stat.st_ino != current_stat.st_ino
            ):
                msg = f"Unsafe private key path changed while adopting permissions: {private_path}"
                raise KeyMaterialError("unsafe_path", msg)
            os.fchmod(file_descriptor, 0o600)
            os.fsync(file_descriptor)
        finally:
            os.close(file_descriptor)
        _fsync_directory(keys_dir)
        adopted_private, adopted_public = _read_keypair(
            private_path,
            public_path,
            require_private_mode=True,
        )
        if public_key_to_b64(adopted_public) != public_key_to_b64(public_key):
            msg = f"Public key changed while adopting permissions for handle {handle!r}"
            raise KeyMaterialError("mismatched_pair", msg)
        return adopted_private, adopted_public


def load_private_key(path: Path) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a PEM file.

    Args:
        path: Path to the PEM-encoded private key file.

    Returns:
        The loaded private key.

    Raises:
        FileNotFoundError: If the key file does not exist.
        ValueError: If the file does not contain a valid Ed25519 private key.
    """
    key_bytes = path.read_bytes()
    private_key = serialization.load_pem_private_key(key_bytes, password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        msg = f"Expected Ed25519 private key, got {type(private_key).__name__}"
        raise ValueError(msg)
    return private_key


def load_public_key(path: Path) -> Ed25519PublicKey:
    """Load an Ed25519 public key from a PEM file.

    Args:
        path: Path to the PEM-encoded public key file.

    Returns:
        The loaded public key.

    Raises:
        FileNotFoundError: If the key file does not exist.
        ValueError: If the file does not contain a valid Ed25519 public key.
    """
    key_bytes = path.read_bytes()
    public_key = serialization.load_pem_public_key(key_bytes)
    if not isinstance(public_key, Ed25519PublicKey):
        msg = f"Expected Ed25519 public key, got {type(public_key).__name__}"
        raise ValueError(msg)
    return public_key


def public_key_to_b64(public_key: Ed25519PublicKey) -> str:
    """Export a public key as base64-encoded raw bytes.

    The Identity service expects keys in the format "ed25519:<base64>".
    This function returns only the base64 portion.

    Args:
        public_key: The Ed25519 public key to export.

    Returns:
        Base64-encoded string of the raw 32-byte public key.
    """
    raw_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(raw_bytes).decode("ascii")


class PlatformSigner:
    """Signs outgoing platform JWS tokens with a disk-loaded Ed25519 key.

    Used by services that act as the platform agent for privileged operations
    (e.g. the Task Board releasing escrow at the Central Bank). Produces tokens
    with the single system-wide canonicalization via :func:`create_jws`.
    """

    def __init__(
        self,
        platform_agent_id: str,
        private_key_path: str,
        token_ttl_seconds: int,
    ) -> None:
        if (
            isinstance(token_ttl_seconds, bool)
            or not isinstance(token_ttl_seconds, int)
            or token_ttl_seconds <= 0
        ):
            msg = "token_ttl_seconds must be a positive integer"
            raise ValueError(msg)
        key_path = Path(private_key_path)
        if key_path.suffix != ".key" or not key_path.stem:
            msg = "private_key_path must use the canonical '<handle>.key' filename"
            raise ValueError(msg)
        self._agent_id = platform_agent_id
        self._private_key, _public_key = load_keypair(key_path.stem, key_path.parent)
        self._token_ttl_seconds = token_ttl_seconds

    def sign(
        self,
        payload: dict[str, object],
        *,
        audience: str,
        resource: str,
    ) -> str:
        """Create a compact JWS token signed as the platform agent.

        Args:
            payload: The JWS payload as a dict. Must include an "action" field
                (e.g. "escrow_release").

        Returns:
            Compact JWS string (header.payload.signature).
        """
        return create_jws(
            payload,
            self._private_key,
            kid=self._agent_id,
            audience=audience,
            resource=resource,
            token_ttl_seconds=self._token_ttl_seconds,
        )

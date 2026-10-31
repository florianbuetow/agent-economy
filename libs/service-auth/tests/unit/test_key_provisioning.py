"""Acceptance tests for durable key-pair provisioning and loading (T-114)."""

from __future__ import annotations

import base64
import hashlib
import multiprocessing
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from service_auth import signing
from service_auth.signing import generate_keypair, public_key_to_b64

if TYPE_CHECKING:
    from collections.abc import Callable


HANDLE = "durable-agent"


def _pair_paths(keys_dir: Path, handle: str = HANDLE) -> tuple[Path, Path]:
    return keys_dir / f"{handle}.key", keys_dir / f"{handle}.pub"


def _pair_bytes(keys_dir: Path, handle: str = HANDLE) -> tuple[bytes, bytes]:
    private_path, public_path = _pair_paths(keys_dir, handle)
    return private_path.read_bytes(), public_path.read_bytes()


def _fingerprint(public_key: Any) -> str:
    raw_public = base64.b64decode(public_key_to_b64(public_key))
    return hashlib.sha256(raw_public).hexdigest()


def _require_pair_loader() -> Callable[[str, Path], tuple[Any, Any]]:
    loader = getattr(signing, "load_keypair", None)
    assert callable(loader), (
        "MP-03 requires service_auth.signing.load_keypair(handle, keys_dir) so "
        "consumers validate both halves through one load-only API"
    )
    return loader


def _concurrent_generate(
    keys_dir: str,
    barrier: multiprocessing.synchronize.Barrier,
    results: multiprocessing.queues.Queue,
) -> None:
    """Cold-start worker used by the real multiprocess convergence test."""
    try:
        barrier.wait(timeout=10)
        _, public_key = generate_keypair(HANDLE, Path(keys_dir))
        results.put(("ok", _fingerprint(public_key)))
    except BaseException as exc:  # pragma: no cover - only reported to the parent
        results.put(("error", f"{type(exc).__name__}: {exc}"))


def _paused_publication_writer(
    keys_dir: str,
    writer_go: multiprocessing.synchronize.Event,
    public_linked: multiprocessing.synchronize.Event,
    allow_private: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    real_link = signing.os.link

    def paused_link(source: Path, destination: Path) -> None:
        real_link(source, destination)
        if Path(destination).suffix == ".pub":
            public_linked.set()
            if not allow_private.wait(timeout=10):
                raise TimeoutError("reader did not release private publication")

    try:
        if not writer_go.wait(timeout=10):
            raise TimeoutError("reader did not start writer")
        signing.os.link = paused_link
        _, public_key = generate_keypair(HANDLE, Path(keys_dir))
        results.put(("writer", "ok", _fingerprint(public_key)))
    except BaseException as exc:  # pragma: no cover - only reported to the parent
        results.put(("writer", "error", f"{type(exc).__name__}: {exc}"))


def _missing_lock_reader(
    keys_dir: str,
    writer_go: multiprocessing.synchronize.Event,
    public_linked: multiprocessing.synchronize.Event,
    results: multiprocessing.queues.Queue,
) -> None:
    real_path_kind = signing._path_kind
    lock_checks = 0

    def coordinated_path_kind(path: Path) -> str:
        nonlocal lock_checks
        if path.suffix != ".lock":
            return real_path_kind(path)
        lock_checks += 1
        if lock_checks == 1:
            return "missing"
        writer_go.set()
        if not public_linked.wait(timeout=10):
            raise TimeoutError("writer did not publish public half")
        return "missing"

    try:
        signing._path_kind = coordinated_path_kind
        _, public_key = signing.load_keypair(HANDLE, Path(keys_dir))
        results.put(("reader", "ok", _fingerprint(public_key)))
    except signing.KeyMaterialError as exc:
        results.put(("reader", "key_error", exc.reason))
    except BaseException as exc:  # pragma: no cover - only reported to the parent
        results.put(("reader", "error", f"{type(exc).__name__}: {exc}"))


@pytest.mark.unit
def test_generate_keypair_is_idempotent_and_never_replaces_existing_pair(tmp_path: Path) -> None:
    _, original_public = generate_keypair(HANDLE, tmp_path)
    original_bytes = _pair_bytes(tmp_path)
    original_fingerprint = _fingerprint(original_public)

    _, repeated_public = generate_keypair(HANDLE, tmp_path)

    assert _fingerprint(repeated_public) == original_fingerprint
    assert _pair_bytes(tmp_path) == original_bytes


@pytest.mark.unit
def test_cold_concurrent_generate_keypair_converges_across_processes(tmp_path: Path) -> None:
    process_count = 4
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(process_count)
    results = context.Queue()
    processes = [
        context.Process(target=_concurrent_generate, args=(str(tmp_path), barrier, results))
        for _ in range(process_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
    try:
        assert all(not process.is_alive() for process in processes), "provisioners hung"
        assert all(process.exitcode == 0 for process in processes)
        outcomes = [results.get(timeout=2) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        results.join_thread()

    assert all(status == "ok" for status, _ in outcomes), outcomes
    fingerprints = {value for _, value in outcomes}
    assert len(fingerprints) == 1, outcomes

    private_path, public_path = _pair_paths(tmp_path)
    loader = _require_pair_loader()
    _, final_public = loader(HANDLE, tmp_path)
    assert _fingerprint(final_public) in fingerprints
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o600
    assert public_path.is_file()
    assert {path.name for path in tmp_path.iterdir()} <= {
        f"{HANDLE}.key",
        f"{HANDLE}.pub",
        f"{HANDLE}.lock",
    }


@pytest.mark.unit
@pytest.mark.parametrize("surviving_half", ["private", "public"])
def test_partial_pair_is_refused_without_changing_surviving_bytes(
    tmp_path: Path,
    surviving_half: str,
) -> None:
    generate_keypair(HANDLE, tmp_path)
    private_path, public_path = _pair_paths(tmp_path)
    missing_path = public_path if surviving_half == "private" else private_path
    surviving_path = private_path if surviving_half == "private" else public_path
    missing_path.unlink()
    surviving_bytes = surviving_path.read_bytes()

    with pytest.raises(ValueError, match=r"(?i)incomplete|partial|missing"):
        generate_keypair(HANDLE, tmp_path)

    assert surviving_path.read_bytes() == surviving_bytes
    assert not missing_path.exists()


@pytest.mark.unit
@pytest.mark.parametrize("corruption", ["private", "public", "both"])
def test_corrupt_pair_is_refused_without_replacement(tmp_path: Path, corruption: str) -> None:
    generate_keypair(HANDLE, tmp_path)
    private_path, public_path = _pair_paths(tmp_path)
    if corruption in {"private", "both"}:
        private_path.write_bytes(b"not a private PEM\n")
    if corruption in {"public", "both"}:
        public_path.write_bytes(b"not a public PEM\n")
    corrupt_bytes = _pair_bytes(tmp_path)

    with pytest.raises(ValueError, match=r"(?i)invalid|corrupt|key"):
        generate_keypair(HANDLE, tmp_path)

    assert _pair_bytes(tmp_path) == corrupt_bytes


@pytest.mark.unit
def test_mismatched_pair_is_refused_without_replacement(tmp_path: Path) -> None:
    pair_a = tmp_path / "pair-a"
    pair_b = tmp_path / "pair-b"
    target = tmp_path / "target"
    generate_keypair(HANDLE, pair_a)
    generate_keypair(HANDLE, pair_b)
    target.mkdir()
    private_path, public_path = _pair_paths(target)
    private_path.write_bytes(_pair_paths(pair_a)[0].read_bytes())
    private_path.chmod(0o600)
    public_path.write_bytes(_pair_paths(pair_b)[1].read_bytes())
    mismatched_bytes = _pair_bytes(target)

    with pytest.raises(ValueError, match=r"(?i)mismatch|does not match"):
        generate_keypair(HANDLE, target)

    assert _pair_bytes(target) == mismatched_bytes


@pytest.mark.unit
def test_load_keypair_rejects_mismatched_pair_without_rewrite(tmp_path: Path) -> None:
    pair_a = tmp_path / "pair-a"
    pair_b = tmp_path / "pair-b"
    target = tmp_path / "target"
    generate_keypair(HANDLE, pair_a)
    generate_keypair(HANDLE, pair_b)
    target.mkdir()
    private_path, public_path = _pair_paths(target)
    private_path.write_bytes(_pair_paths(pair_a)[0].read_bytes())
    private_path.chmod(0o600)
    public_path.write_bytes(_pair_paths(pair_b)[1].read_bytes())
    mismatched_bytes = _pair_bytes(target)

    loader = _require_pair_loader()
    with pytest.raises(ValueError, match=r"(?i)mismatch|does not match"):
        loader(HANDLE, target)

    assert _pair_bytes(target) == mismatched_bytes


@pytest.mark.unit
def test_fresh_private_key_mode_is_exactly_0600_under_permissive_umask(tmp_path: Path) -> None:
    previous_umask = os.umask(0)
    try:
        generate_keypair(HANDLE, tmp_path)
    finally:
        os.umask(previous_umask)

    private_path, _ = _pair_paths(tmp_path)
    assert stat.S_IMODE(private_path.stat().st_mode) == 0o600


@pytest.mark.unit
@pytest.mark.parametrize("unsafe_handle", ["", ".", "..", "nested/agent", r"nested\agent"])
def test_generate_keypair_rejects_unsafe_handle_without_writing(
    tmp_path: Path,
    unsafe_handle: str,
) -> None:
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()

    with pytest.raises(ValueError, match=r"(?i)unsafe|invalid|handle"):
        generate_keypair(unsafe_handle, keys_dir)

    assert list(keys_dir.iterdir()) == []


@pytest.mark.unit
@pytest.mark.parametrize("symlink_half", ["private", "public", "lock"])
def test_generate_keypair_rejects_symlinked_key_store_entries(
    tmp_path: Path,
    symlink_half: str,
) -> None:
    keys_dir = tmp_path / "keys"
    keys_dir.mkdir()
    outside = tmp_path / f"outside-{symlink_half}"
    outside_bytes = f"do not touch {symlink_half}\n".encode()
    outside.write_bytes(outside_bytes)
    private_path, public_path = _pair_paths(keys_dir)
    targets = {
        "private": private_path,
        "public": public_path,
        "lock": keys_dir / f"{HANDLE}.lock",
    }
    targets[symlink_half].symlink_to(outside)

    error: ValueError | None = None
    try:
        generate_keypair(HANDLE, keys_dir)
    except ValueError as exc:
        error = exc

    assert outside.read_bytes() == outside_bytes
    assert error is not None
    assert any(word in str(error).lower() for word in ("unsafe", "symlink", "regular"))


@pytest.mark.unit
def test_interrupted_public_first_publication_remains_partial_and_is_not_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing_os = getattr(signing, "os", None)
    assert signing_os is not None and hasattr(signing_os, "link"), (
        "MP-03 provisioning must use no-replace same-directory publication"
    )
    real_link = signing_os.link

    def fail_private_publication(source: Path, destination: Path) -> None:
        if Path(destination).suffix == ".key":
            raise OSError("injected failure before private commit marker")
        real_link(source, destination)

    with monkeypatch.context() as patcher:
        patcher.setattr(signing_os, "link", fail_private_publication)
        with pytest.raises(OSError, match="injected failure"):
            generate_keypair(HANDLE, tmp_path)

    private_path, public_path = _pair_paths(tmp_path)
    assert public_path.is_file()
    assert not private_path.exists()
    published_public = public_path.read_bytes()

    with pytest.raises(ValueError, match=r"(?i)incomplete|partial|missing"):
        generate_keypair(HANDLE, tmp_path)

    assert public_path.read_bytes() == published_public
    assert not private_path.exists()


@pytest.mark.unit
def test_publication_fsyncs_public_before_private_commit_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    real_link = signing.os.link
    real_fsync_directory = signing._fsync_directory

    def recording_link(source: Path, destination: Path) -> None:
        events.append(f"link:{Path(destination).suffix}")
        real_link(source, destination)

    def recording_fsync(keys_dir: Path) -> None:
        events.append("fsync:dir")
        real_fsync_directory(keys_dir)

    monkeypatch.setattr(signing.os, "link", recording_link)
    monkeypatch.setattr(signing, "_fsync_directory", recording_fsync)

    generate_keypair(HANDLE, tmp_path)

    assert events[:4] == ["link:.pub", "fsync:dir", "link:.key", "fsync:dir"]


@pytest.mark.unit
def test_missing_lock_reader_never_observes_publication_in_progress(tmp_path: Path) -> None:
    context = multiprocessing.get_context("spawn")
    writer_go = context.Event()
    public_linked = context.Event()
    allow_private = context.Event()
    results = context.Queue()
    writer = context.Process(
        target=_paused_publication_writer,
        args=(str(tmp_path), writer_go, public_linked, allow_private, results),
    )
    reader = context.Process(
        target=_missing_lock_reader,
        args=(str(tmp_path), writer_go, public_linked, results),
    )
    writer.start()
    reader.start()

    reader.join(timeout=10)
    if not writer_go.is_set():
        writer_go.set()
    reader_outcome = results.get(timeout=5)
    allow_private.set()
    writer.join(timeout=10)
    writer_outcome = results.get(timeout=5)
    try:
        assert not reader.is_alive()
        assert not writer.is_alive()
        assert reader.exitcode == 0
        assert writer.exitcode == 0
        assert reader_outcome[0] == "reader"
        assert reader_outcome[1:] in {("key_error", "absent_pair"), writer_outcome[1:]}
        assert writer_outcome[0:2] == ("writer", "ok")
    finally:
        allow_private.set()
        for process in (reader, writer):
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        results.close()
        results.join_thread()


def _descriptor_is_closed(file_descriptor: int) -> bool:
    try:
        os.fstat(file_descriptor)
    except OSError:
        return True
    return False


@pytest.mark.unit
def test_lock_acquisition_failure_closes_descriptor_and_preserves_diagnostic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_descriptor: list[int] = []

    def failing_flock(file_descriptor: int, operation: int) -> None:
        captured_descriptor.append(file_descriptor)
        if operation == signing.fcntl.LOCK_EX:
            raise OSError("injected acquisition failure")
        raise OSError("unlock must not mask acquisition failure")

    monkeypatch.setattr(signing.fcntl, "flock", failing_flock)
    caught: OSError | None = None
    try:
        generate_keypair(HANDLE, tmp_path)
    except OSError as exc:
        caught = exc

    assert caught is not None
    descriptor = captured_descriptor[0]
    descriptor_closed = _descriptor_is_closed(descriptor)
    if not descriptor_closed:
        os.close(descriptor)
    assert "acquisition failure" in str(caught)
    assert descriptor_closed


@pytest.mark.unit
def test_unlock_failure_does_not_mask_body_failure_and_descriptor_is_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_flock = signing.fcntl.flock
    captured_descriptor: list[int] = []

    def failing_unlock(file_descriptor: int, operation: int) -> None:
        captured_descriptor.append(file_descriptor)
        if operation == signing.fcntl.LOCK_UN:
            raise OSError("injected unlock failure")
        real_flock(file_descriptor, operation)

    def failing_stage(*_args: object, **_kwargs: object) -> Path:
        raise OSError("injected body failure")

    monkeypatch.setattr(signing.fcntl, "flock", failing_unlock)
    monkeypatch.setattr(signing, "_write_staged_file", failing_stage)
    caught: OSError | None = None
    try:
        generate_keypair(HANDLE, tmp_path)
    except OSError as exc:
        caught = exc

    assert caught is not None
    descriptor = captured_descriptor[0]
    descriptor_closed = _descriptor_is_closed(descriptor)
    if not descriptor_closed:
        os.close(descriptor)
    assert "body failure" in str(caught)
    assert descriptor_closed

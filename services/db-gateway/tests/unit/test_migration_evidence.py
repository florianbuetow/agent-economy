"""Acceptance tests for retained migration evidence in a bound recovery workspace."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from db_gateway_service import recovery
from tests.unit import test_mp06_recovery as recovery_tests
from tests.unit.test_migration_source import _published_source

if TYPE_CHECKING:
    from collections.abc import Iterator


pytestmark = pytest.mark.unit


@pytest.fixture
def bound_source(
    tmp_path: Path,
) -> Iterator[tuple[recovery.BackupBoundSource, Path, Path]]:
    """Yield a write-phase source plus its immutable artifact and workspace roots."""

    inventory, backup_root, work_root = _published_source(
        tmp_path,
        backup_id="migration-evidence",
        inventory_update=None,
    )
    with recovery.open_backup_bound_source(
        inventory,
        backup_root=backup_root,
        backup_id="migration-evidence",
        work_root=work_root,
    ) as source:
        source.begin_write_phase()
        yield source, backup_root, work_root


def test_bound_source_exposes_write_evidence_api() -> None:
    """The migration entry point has an explicit retained-evidence operation."""

    assert callable(recovery.BackupBoundSource.write_evidence)


def test_write_evidence_requires_live_write_phase(tmp_path: Path) -> None:
    """Evidence cannot be created before migration enters its write phase or after close."""

    inventory, backup_root, work_root = _published_source(
        tmp_path,
        backup_id="evidence-phase",
        inventory_update=None,
    )
    with recovery.open_backup_bound_source(
        inventory,
        backup_root=backup_root,
        backup_id="evidence-phase",
        work_root=work_root,
    ) as source:
        with pytest.raises(recovery.RecoveryError) as phase_error:
            source.write_evidence("migration-result.json", b"{}")
        assert phase_error.value.reason == "invalid_phase"
        source.begin_write_phase()
    with pytest.raises(recovery.RecoveryError) as closed_error:
        source.write_evidence("migration-result.json", b"{}")
    assert closed_error.value.reason == "context_closed"


def test_write_evidence_is_private_exact_and_retained_after_close(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
) -> None:
    """Canonical evidence bytes remain at a stable private path after context cleanup."""

    source, _backup_root, _work_root = bound_source
    clone_parent = source.database_path.parent
    evidence_path = source.write_evidence("migration-result.json", b'{"result":"verified"}\n')
    assert evidence_path.parent == clone_parent
    assert evidence_path.read_bytes() == b'{"result":"verified"}\n'
    assert evidence_path.stat().st_mode & 0o777 == 0o600
    assert evidence_path.parent.stat().st_mode & 0o777 == 0o700


def test_write_evidence_path_remains_readable_after_context_close(tmp_path: Path) -> None:
    """The returned path is not descriptor-only and remains usable after lease release."""

    inventory, backup_root, work_root = _published_source(
        tmp_path,
        backup_id="evidence-stable-path",
        inventory_update=None,
    )
    with recovery.open_backup_bound_source(
        inventory,
        backup_root=backup_root,
        backup_id="evidence-stable-path",
        work_root=work_root,
    ) as source:
        source.begin_write_phase()
        evidence_path = source.write_evidence("migration-result.json", b"retained")
    assert evidence_path.is_file()
    assert evidence_path.read_bytes() == b"retained"
    assert evidence_path.stat().st_mode & 0o777 == 0o600


def test_write_evidence_is_exclusive_and_never_overwrites(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
) -> None:
    """Repeating an evidence name fails closed and preserves the original bytes."""

    source, _backup_root, _work_root = bound_source
    evidence_path = source.write_evidence("migration-result.json", b"first")
    with pytest.raises(recovery.RecoveryError) as error:
        source.write_evidence("migration-result.json", b"second")
    assert error.value.reason == "write_failed"
    assert evidence_path.read_bytes() == b"first"


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "nested/result.json",
        "nested\\result.json",
        ".owner.json",
        "economy.db",
        "economy.db-wal",
    ],
)
def test_write_evidence_rejects_unsafe_and_reserved_names(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
    name: str,
) -> None:
    """Evidence cannot escape scratch or overwrite its control and clone files."""

    source, _backup_root, _work_root = bound_source
    with pytest.raises(recovery.RecoveryError) as error:
        source.write_evidence(name, b"payload")
    assert error.value.reason == "invalid_argument"


def test_write_evidence_rejects_nul_name_without_creating_output(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
) -> None:
    """An embedded NUL is rejected as an argument before filesystem access."""

    source, _backup_root, _work_root = bound_source
    scratch_path = source.database_path.parent
    entries_before = {entry.name for entry in scratch_path.iterdir()}
    with pytest.raises(recovery.RecoveryError) as error:
        source.write_evidence("bad\x00name", b"payload")
    assert error.value.reason == "invalid_argument"
    assert {entry.name for entry in scratch_path.iterdir()} == entries_before


def test_backup_id_rejects_nul_before_workspace_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared component rule rejects NUL-bearing backup IDs before filesystem work."""

    inventory, connection = recovery_tests._source(tmp_path)
    connection.close()
    probed = False

    def reject_workspace_probe(_work_root: Path) -> recovery._RecoveryWorkspace:
        nonlocal probed
        probed = True
        raise AssertionError("workspace must not be probed")

    monkeypatch.setattr(recovery, "_open_workspace", reject_workspace_probe)
    with (
        pytest.raises(recovery.RecoveryError) as error,
        recovery.open_backup_bound_source(
            inventory,
            backup_root=tmp_path / "backups",
            backup_id="bad\x00backup",
            work_root=tmp_path / "work",
        ),
    ):
        pass
    assert error.value.reason == "invalid_argument"
    assert probed is False


@pytest.mark.parametrize("payload", [bytearray(b"mutable"), "text", object()])
def test_write_evidence_requires_immutable_bytes(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
    payload: object,
) -> None:
    """Callers must provide an exact immutable byte representation."""

    source, _backup_root, _work_root = bound_source
    with pytest.raises(TypeError, match="payload must be bytes"):
        source.write_evidence("migration-result.json", payload)  # type: ignore[arg-type]


def test_write_evidence_rechecks_source_identity_before_creation(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
) -> None:
    """A changed live source blocks evidence publication before any output is created."""

    source, _backup_root, _work_root = bound_source
    original_mode = source.source_database_path.stat().st_mode & 0o777
    source.source_database_path.chmod(0o600)
    try:
        with pytest.raises(recovery.RecoveryError) as error:
            source.write_evidence("migration-result.json", b"blocked")
        assert error.value.reason == "source_changed"
        assert not (source.database_path.parent / "migration-result.json").exists()
    finally:
        source.source_database_path.chmod(original_mode)


def test_write_evidence_rechecks_artifact_before_creation(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
) -> None:
    """Immutable backup tampering blocks evidence publication."""

    source, backup_root, _work_root = bound_source
    marker = backup_root / "migration-evidence/COMPLETE"
    original = marker.read_bytes()
    marker.write_bytes(original + b" ")
    try:
        with pytest.raises(recovery.RecoveryError):
            source.write_evidence("migration-result.json", b"blocked")
        assert not (source.database_path.parent / "migration-result.json").exists()
    finally:
        marker.write_bytes(original)


def test_write_evidence_rechecks_workspace_lease_before_creation(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
) -> None:
    """A changed workspace lease identity or mode blocks evidence publication."""

    source, _backup_root, work_root = bound_source
    lock_path = work_root / ".recovery-owner.lock"
    lock_path.chmod(0o640)
    try:
        with pytest.raises(recovery.RecoveryError) as error:
            source.write_evidence("migration-result.json", b"blocked")
        assert error.value.reason == "workspace_changed"
        assert not (source.database_path.parent / "migration-result.json").exists()
    finally:
        lock_path.chmod(0o600)


def test_write_evidence_rechecks_scratch_owner_before_creation(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
) -> None:
    """A changed scratch ownership marker blocks evidence publication."""

    source, _backup_root, _work_root = bound_source
    owner_marker = source.database_path.parent / ".owner.json"
    original = owner_marker.read_bytes()
    owner_marker.write_bytes(original + b" ")
    try:
        with pytest.raises(recovery.RecoveryError) as error:
            source.write_evidence("migration-result.json", b"blocked")
        assert error.value.reason == "cleanup_failed"
        assert not (source.database_path.parent / "migration-result.json").exists()
    finally:
        owner_marker.write_bytes(original)


def test_write_evidence_rechecks_source_after_creation_and_retains_failure_evidence(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-write source race fails closed without deleting the newly written evidence."""

    source, _backup_root, _work_root = bound_source
    original_write = recovery._write_new_file_under
    original_mode = source.source_database_path.stat().st_mode & 0o777

    def write_then_change_source(
        root: recovery._PinnedDirectory,
        relative: Path,
        data: bytes,
        *,
        mode: int,
    ) -> Path:
        result = original_write(root, relative, data, mode=mode)
        if relative == Path("migration-result.json"):
            source.source_database_path.chmod(0o600)
        return result

    monkeypatch.setattr(recovery, "_write_new_file_under", write_then_change_source)
    try:
        with pytest.raises(recovery.RecoveryError) as error:
            source.write_evidence("migration-result.json", b"retained-on-race")
        assert error.value.reason == "source_changed"
        evidence_path = source.database_path.parent / "migration-result.json"
        assert evidence_path.read_bytes() == b"retained-on-race"
        assert evidence_path.stat().st_mode & 0o777 == 0o600
    finally:
        source.source_database_path.chmod(original_mode)


def test_write_evidence_fsync_failure_is_normalized_and_file_is_retained(
    bound_source: tuple[recovery.BackupBoundSource, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scratch durability failure is explicit and never triggers unsafe cleanup."""

    source, _backup_root, _work_root = bound_source
    original_fsync = recovery.os.fsync
    scratch_descriptor = source._scratch.directory.descriptor

    def fail_scratch_fsync(descriptor: int) -> None:
        if descriptor == scratch_descriptor:
            raise OSError("injected scratch fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(recovery.os, "fsync", fail_scratch_fsync)
    with pytest.raises(recovery.RecoveryError) as error:
        source.write_evidence("migration-result.json", b"retained-on-fsync-failure")
    assert error.value.reason == "write_failed"
    evidence_path = source.database_path.parent / "migration-result.json"
    assert evidence_path.read_bytes() == b"retained-on-fsync-failure"
    assert evidence_path.stat().st_mode & 0o777 == 0o600

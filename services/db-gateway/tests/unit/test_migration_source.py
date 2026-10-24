"""Tests for the backup-bound live-source identity gate."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from service_commons.maintenance_lock import (
    MaintenanceLockError,
    MaintenanceLockMode,
    hold_maintenance_lock,
)

from db_gateway_service import recovery
from tests.unit import test_mp06_recovery as recovery_tests

if TYPE_CHECKING:
    from service_commons.recovery_contract import RecoveryInventoryV1


def _published_source(
    tmp_path: Path,
    *,
    backup_id: str,
    inventory_update: dict[str, object] | None,
) -> tuple[RecoveryInventoryV1, Path, Path]:
    inventory, connection = recovery_tests._source(tmp_path)
    connection.close()
    if inventory_update is not None:
        inventory = inventory.model_copy(update=inventory_update)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )
    return inventory, backup_root, work_root


def test_backup_bound_source_api_is_available() -> None:
    """The implementation starts with an explicit public context API."""

    assert callable(recovery.open_backup_bound_source)


def test_valid_context_exposes_disposable_clone_and_holds_exclusive_lock(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="valid", inventory_update=None
    )
    with recovery.open_backup_bound_source(
        inventory,
        backup_root=backup_root,
        backup_id="valid",
        work_root=work_root,
    ) as source:
        assert source.artifact_verification_result.artifact_verified is True
        assert source.artifact_verification_result.snapshot_preserved is True
        assert source.artifact_verification_result.identity_complete is True
        assert source.artifact_verification_result.migration_eligible is False
        assert source.source_database_path == inventory.source_root / "data/economy.db"
        assert source.database_path.is_file()
        with sqlite3.connect(source.database_path) as clone:
            assert clone.execute("SELECT value FROM proof").fetchall() == [("committed-in-wal",)]
        with (
            pytest.raises(MaintenanceLockError) as busy,
            hold_maintenance_lock(
                inventory.maintenance_lock_path,
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
            ),
        ):
            pass
        assert busy.value.reason == "busy"
        clone_path = source.database_path
        source_descriptor = source._state.main_descriptor
        scratch_descriptor = source._scratch.directory.descriptor
        source.verify_before_write()
        with pytest.raises(FrozenInstanceError):
            source.backup_id = "changed"  # type: ignore[misc]
    assert clone_path.is_file()
    assert clone_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(OSError):
        os.fstat(source_descriptor)
    with pytest.raises(OSError):
        os.fstat(scratch_descriptor)
    with pytest.raises(recovery.RecoveryError) as closed:
        source.verify_before_write()
    assert closed.value.reason == "context_closed"


def test_context_never_opens_live_database_for_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="read-only-source", inventory_update=None
    )
    database = inventory.source_root / "data/economy.db"
    original_open = recovery.os.open
    observed_access_modes: list[int] = []

    def record_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        directory_descriptor = kwargs.get("dir_fd")
        database_parent = database.parent.stat()
        source_relative = (
            path == database.name
            and isinstance(directory_descriptor, int)
            and os.fstat(directory_descriptor).st_dev == database_parent.st_dev
            and os.fstat(directory_descriptor).st_ino == database_parent.st_ino
        )
        if path == database or source_relative:
            observed_access_modes.append(flags & os.O_ACCMODE)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(recovery.os, "open", record_open)
    with recovery.open_backup_bound_source(
        inventory,
        backup_root=backup_root,
        backup_id="read-only-source",
        work_root=work_root,
    ):
        pass
    assert observed_access_modes
    assert set(observed_access_modes) == {os.O_RDONLY}


@pytest.mark.parametrize("damage", ["missing_marker", "missing_payload", "tampered_payload"])
def test_context_rejects_incomplete_or_tampered_artifact(tmp_path: Path, damage: str) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id=damage, inventory_update=None
    )
    artifact = backup_root / damage
    if damage == "missing_marker":
        (artifact / "COMPLETE").unlink()
    elif damage == "missing_payload":
        (artifact / "payload/database/economy.db").unlink()
    else:
        database = artifact / "payload/database/economy.db"
        database.write_bytes(database.read_bytes() + b"tamper")
    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id=damage,
            work_root=work_root,
        ),
    ):
        pass


def test_context_rejects_identity_incomplete_artifact(tmp_path: Path) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path,
        backup_id="identity-incomplete",
        inventory_update={"keypairs": ()},
    )
    with (
        pytest.raises(recovery.RecoveryError) as error,
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="identity-incomplete",
            work_root=work_root,
        ),
    ):
        pass
    assert error.value.reason == "identity_incomplete"


def test_context_rejects_current_inventory_that_differs_from_backup(tmp_path: Path) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="inventory", inventory_update=None
    )
    changed = inventory.model_copy(update={"runtime_id": "different-runtime"})
    with (
        pytest.raises(recovery.RecoveryError) as error,
        recovery.open_backup_bound_source(
            changed,
            backup_root=backup_root,
            backup_id="inventory",
            work_root=work_root,
        ),
    ):
        pass
    assert error.value.reason == "inventory_mismatch"


@pytest.mark.parametrize("damage", ["bytes", "mode", "inode", "symlink", "wal_presence"])
def test_context_rejects_live_source_mismatch(tmp_path: Path, damage: str) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id=f"source-{damage}", inventory_update=None
    )
    database = inventory.source_root / "data/economy.db"
    if damage == "bytes":
        database.write_bytes(database.read_bytes() + b"changed")
    elif damage == "mode":
        database.chmod(0o640)
    elif damage == "wal_presence":
        Path(f"{database}-wal").write_bytes(b"unexpected")
    else:
        original = database.with_suffix(".original")
        database.rename(original)
        if damage == "inode":
            database.write_bytes(original.read_bytes())
        else:
            database.symlink_to(original)
    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id=f"source-{damage}",
            work_root=work_root,
        ),
    ):
        pass


@pytest.mark.parametrize("lock_state", ["busy", "replaced"])
def test_context_rejects_busy_or_replaced_maintenance_lock(tmp_path: Path, lock_state: str) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id=f"lock-{lock_state}", inventory_update=None
    )

    if lock_state == "busy":
        with (
            hold_maintenance_lock(
                inventory.maintenance_lock_path,
                mode=MaintenanceLockMode.EXCLUSIVE,
                blocking=False,
            ),
            pytest.raises(recovery.RecoveryError) as error,
            recovery.open_backup_bound_source(
                inventory,
                backup_root=backup_root,
                backup_id=f"lock-{lock_state}",
                work_root=work_root,
            ),
        ):
            pass
        assert error.value.reason == "runtime_active"
    else:
        inventory.maintenance_lock_path.unlink()
        with hold_maintenance_lock(
            inventory.maintenance_lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
        ):
            pass
        with (
            pytest.raises(recovery.RecoveryError) as error,
            recovery.open_backup_bound_source(
                inventory,
                backup_root=backup_root,
                backup_id=f"lock-{lock_state}",
                work_root=work_root,
            ),
        ):
            pass
        assert error.value.reason == "maintenance_lock_failed"


def test_before_write_rejects_source_or_artifact_mutation(tmp_path: Path) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="mutated", inventory_update=None
    )
    database = inventory.source_root / "data/economy.db"
    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="mutated",
            work_root=work_root,
        ) as source,
    ):
        database.write_bytes(database.read_bytes() + b"changed")
        source.verify_before_write()

    inventory, backup_root, work_root = _published_source(
        tmp_path / "artifact-case", backup_id="artifact-mutated", inventory_update=None
    )
    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="artifact-mutated",
            work_root=work_root,
        ) as source,
    ):
        manifest = backup_root / "artifact-mutated/payload/manifest.json"
        manifest.write_bytes(manifest.read_bytes() + b" ")
        source.verify_before_write()


def test_write_phase_allows_content_change_but_rejects_main_inode_replacement(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="write-phase", inventory_update=None
    )
    database = inventory.source_root / "data/economy.db"
    with (
        pytest.raises(recovery.RecoveryError) as exit_error,
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="write-phase",
            work_root=work_root,
        ) as source,
    ):
        source.begin_write_phase()
        with pytest.raises(recovery.RecoveryError) as repeated:
            source.begin_write_phase()
        assert repeated.value.reason == "invalid_phase"
        with pytest.raises(recovery.RecoveryError) as stale_precheck:
            source.verify_before_write()
        assert stale_precheck.value.reason == "invalid_phase"
        with database.open("ab") as stream:
            stream.write(b"owned-write")
        source.verify_during_write()
        original = database.with_suffix(".before-replacement")
        database.rename(original)
        database.write_bytes(original.read_bytes())
        with pytest.raises(recovery.RecoveryError) as error:
            source.verify_during_write()
        assert error.value.reason == "source_changed"
    assert exit_error.value.reason == "source_changed"


def test_source_descriptor_cleanup_failure_releases_lock_and_removes_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="cleanup", inventory_update=None
    )
    original_close = recovery.os.close
    source_descriptor: int | None = None
    clone_path: Path | None = None

    def fail_source_close(descriptor: int) -> None:
        if descriptor == source_descriptor:
            original_close(descriptor)
            raise OSError("injected source close failure")
        original_close(descriptor)

    with (
        pytest.raises(recovery.RecoveryError) as error,
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="cleanup",
            work_root=work_root,
        ) as source,
    ):
        source_descriptor = source._state.main_descriptor
        clone_path = source.database_path
        monkeypatch.setattr(recovery.os, "close", fail_source_close)
    assert error.value.reason == "close_failed"
    assert clone_path is not None
    assert clone_path.is_file()
    monkeypatch.undo()
    with hold_maintenance_lock(
        inventory.maintenance_lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass


@pytest.mark.parametrize("damage", ["removed", "tampered", "alias_added"])
def test_authority_checks_reject_completion_marker_changes_after_entry(
    tmp_path: Path, damage: str
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id=f"marker-{damage}", inventory_update=None
    )
    marker_path = backup_root / f"marker-{damage}/COMPLETE"
    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id=f"marker-{damage}",
            work_root=work_root,
        ) as source,
    ):
        if damage == "removed":
            marker_path.unlink()
        elif damage == "tampered":
            marker_path.write_bytes(marker_path.read_bytes() + b" ")
        else:
            marker = json.loads(marker_path.read_bytes())
            os.link(marker_path, marker_path.parent / marker["temporary_marker_name"])
        source.begin_write_phase()


def test_work_root_replacement_cannot_redirect_clone_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="work-race", inventory_update=None
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside")
    displaced = tmp_path / "displaced-work"
    original_mkdir = recovery.os.mkdir
    raced = False

    def race_mkdir(path: object, mode: int, *, dir_fd: int | None = None) -> None:
        nonlocal raced
        if isinstance(path, str) and path.startswith(".mp06-bound-source-") and not raced:
            raced = True
            work_root.rename(displaced)
            work_root.symlink_to(outside, target_is_directory=True)
        original_mkdir(path, mode, dir_fd=dir_fd)

    source_before = (inventory.source_root / "data/economy.db").read_bytes()
    monkeypatch.setattr(recovery.os, "mkdir", race_mkdir)
    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="work-race",
            work_root=work_root,
        ),
    ):
        pass
    assert raced
    assert sentinel.read_bytes() == b"outside"
    assert list(outside.iterdir()) == [sentinel]
    assert any(path.name.startswith(".mp06-bound-source-") for path in displaced.iterdir())
    assert (inventory.source_root / "data/economy.db").read_bytes() == source_before


def test_scratch_cleanup_failure_preserves_primary_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="cleanup-primary", inventory_update=None
    )

    original_close = recovery.os.close
    scratch_descriptor: int | None = None

    def fail_cleanup(descriptor: int) -> None:
        if descriptor == scratch_descriptor:
            original_close(descriptor)
            raise OSError("injected scratch cleanup failure")
        original_close(descriptor)

    with (
        pytest.raises(RuntimeError, match="body-primary") as error,
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="cleanup-primary",
            work_root=work_root,
        ) as source,
    ):
        scratch_descriptor = source._scratch.directory.descriptor
        monkeypatch.setattr(recovery.os, "close", fail_cleanup)
        raise RuntimeError("body-primary")
    assert any("injected scratch cleanup failure" in note for note in error.value.__notes__)


def test_scratch_setup_does_not_adopt_foreign_replacement_after_open_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="setup-foreign", inventory_update=None
    )
    original_open = recovery.os.open
    replaced = False
    foreign: Path | None = None

    def replace_before_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal foreign, replaced
        directory_descriptor = kwargs.get("dir_fd")
        if (
            isinstance(path, str)
            and path.startswith(".mp06-bound-source-")
            and isinstance(directory_descriptor, int)
            and not replaced
        ):
            replaced = True
            owned = recovery._directory_descriptor_path(directory_descriptor) / path
            owned.rmdir()
            owned.mkdir()
            foreign = owned / "foreign"
            foreign.write_bytes(b"foreign")
            raise OSError("injected scratch open failure")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(recovery.os, "open", replace_before_open)
    with (
        pytest.raises(recovery.RecoveryError) as error,
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="setup-foreign",
            work_root=work_root,
        ),
    ):
        pass
    assert error.value.reason == "cleanup_failed"
    assert replaced
    assert foreign is not None
    assert foreign.read_bytes() == b"foreign"


def test_scratch_creation_rejects_replacement_between_snapshot_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="handoff-replacement", inventory_update=None
    )
    original_stat = recovery.os.stat
    replaced = False
    displaced: Path | None = None
    foreign: Path | None = None

    def replace_after_snapshot(
        path: object,
        *args: object,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
        **kwargs: object,
    ) -> os.stat_result:
        nonlocal displaced, foreign, replaced
        result = original_stat(
            path,
            *args,
            dir_fd=dir_fd,
            follow_symlinks=follow_symlinks,
            **kwargs,
        )
        if (
            isinstance(path, str)
            and path.startswith(".mp06-bound-source-")
            and isinstance(dir_fd, int)
            and follow_symlinks is False
            and not replaced
        ):
            replaced = True
            owned = recovery._directory_descriptor_path(dir_fd) / path
            displaced = owned.parent / f"{owned.name}.created"
            owned.rename(displaced)
            owned.mkdir(mode=0o700)
            foreign = owned / "foreign"
            foreign.write_bytes(b"foreign")
        return result

    monkeypatch.setattr(recovery.os, "stat", replace_after_snapshot)
    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="handoff-replacement",
            work_root=work_root,
        ),
    ):
        pass
    assert replaced
    assert foreign is not None
    assert foreign.read_bytes() == b"foreign"
    assert displaced is not None
    assert not (displaced / ".owner.json").exists()


def test_scratch_cleanup_does_not_remove_successful_foreign_replacement(
    tmp_path: Path,
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="cleanup-foreign", inventory_update=None
    )
    replacement: Path | None = None
    displaced: Path | None = None

    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="cleanup-foreign",
            work_root=work_root,
        ) as source,
    ):
        scratch = source._scratch
        owned = scratch.access_path
        displaced = owned.parent / f"{owned.name}.owned"
        owned.rename(displaced)
        owned.mkdir()
        replacement = owned / "sentinel"
        replacement.write_bytes(b"foreign")
    assert replacement is not None
    assert replacement.read_bytes() == b"foreign"
    assert displaced is not None
    assert displaced.is_dir()


def test_during_write_rejects_live_database_mode_change(tmp_path: Path) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="write-mode", inventory_update=None
    )
    database = inventory.source_root / "data/economy.db"
    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="write-mode",
            work_root=work_root,
        ) as source,
    ):
        source.begin_write_phase()
        database.chmod(0o600)
        with pytest.raises(recovery.RecoveryError) as error:
            source.verify_during_write()
        assert error.value.reason == "source_changed"


def test_missing_live_database_is_normalized_to_source_changed(tmp_path: Path) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="missing-live", inventory_update=None
    )
    database = inventory.source_root / "data/economy.db"
    with (
        pytest.raises(recovery.RecoveryError) as exit_error,
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="missing-live",
            work_root=work_root,
        ) as source,
    ):
        source.begin_write_phase()
        database.rename(database.with_suffix(".removed"))
        with pytest.raises(recovery.RecoveryError) as error:
            source.verify_during_write()
        assert error.value.reason == "source_changed"
    assert exit_error.value.reason == "source_changed"


def test_pure_authorization_rejects_before_work_root_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = recovery_tests._source(tmp_path)
    connection.close()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    probed = False

    def reject_probe(_path: Path, *, label: str) -> object:
        nonlocal probed
        probed = True
        raise AssertionError(label)

    monkeypatch.setattr(recovery, "_open_pinned_directory", reject_probe)
    with (
        pytest.raises(TypeError),
        recovery.open_backup_bound_source(  # type: ignore[arg-type]
            object(),
            backup_root=backup_root,
            backup_id="authorization",
            work_root=work_root,
        ),
    ):
        pass
    assert probed is False
    incomplete = inventory.model_copy(
        update={
            "resources": tuple(
                resource
                for resource in inventory.resources
                if resource.logical_id != "runtime.maintenance_lock"
            )
        }
    )
    with (
        pytest.raises(recovery.RecoveryError) as incomplete_error,
        recovery.open_backup_bound_source(
            incomplete,
            backup_root=backup_root,
            backup_id="authorization",
            work_root=work_root,
        ),
    ):
        pass
    assert incomplete_error.value.reason == "incomplete_inventory"
    assert probed is False
    active = inventory.model_copy(
        update={
            "process_ownership": (
                inventory.process_ownership[0].model_copy(update={"running": True}),
            )
        }
    )
    with (
        pytest.raises(recovery.RecoveryError) as active_error,
        recovery.open_backup_bound_source(
            active,
            backup_root=backup_root,
            backup_id="authorization",
            work_root=work_root,
        ),
    ):
        pass
    assert active_error.value.reason == "runtime_not_quiescent"
    assert probed is False
    with (
        pytest.raises(recovery.RecoveryError) as path_error,
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="authorization",
            work_root=Path("relative-work"),
        ),
    ):
        pass
    assert path_error.value.reason == "invalid_argument"
    assert probed is False


def test_descriptor_backed_sqlite_uri_handles_reserved_path_characters(
    tmp_path: Path,
) -> None:
    inventory, connection = recovery_tests._source(tmp_path)
    connection.close()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work ?#%"
    work_root.mkdir(mode=0o700)
    recovery.create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="reserved-path",
        work_root=work_root,
    )
    with (
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="reserved-path",
            work_root=work_root,
        ) as source,
        sqlite3.connect(source.database_path) as clone,
    ):
        assert clone.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@pytest.mark.parametrize("constraint", ["mode", "owner"])
def test_workspace_requires_private_euid_owned_root_without_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, constraint: str
) -> None:
    inventory, connection = recovery_tests._source(tmp_path)
    connection.close()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    if constraint == "mode":
        work_root.chmod(0o755)
    else:
        actual_uid = os.geteuid()
        monkeypatch.setattr(recovery.os, "geteuid", lambda: actual_uid + 1)
    original_mode = work_root.stat().st_mode & 0o777
    with pytest.raises(recovery.RecoveryError) as error:
        recovery.create_backup(
            inventory,
            backup_root=backup_root,
            backup_id=f"workspace-{constraint}",
            work_root=work_root,
        )
    assert error.value.reason == "unsafe_mode"
    assert work_root.stat().st_mode & 0o777 == original_mode
    assert not (work_root / ".recovery-owner.lock").exists()


def test_workspace_lease_is_exclusive_and_persistent(tmp_path: Path) -> None:
    _inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="workspace-busy", inventory_update=None
    )
    lock_path = work_root / ".recovery-owner.lock"
    assert lock_path.is_file()
    assert lock_path.stat().st_mode & 0o777 == 0o600
    with (
        recovery._hold_recovery_workspace(work_root),
        pytest.raises(recovery.RecoveryError) as error,
    ):
        recovery.verify_backup(
            backup_root=backup_root,
            backup_id="workspace-busy",
            work_root=work_root,
        )
    assert error.value.reason == "work_root_busy"
    assert lock_path.is_file()


def test_sequential_contexts_retain_distinct_private_clones(tmp_path: Path) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="sequential", inventory_update=None
    )
    retained: list[Path] = []
    for _index in range(2):
        with recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="sequential",
            work_root=work_root,
        ) as source:
            retained.append(source.database_path)
    assert retained[0] != retained[1]
    assert all(path.is_file() for path in retained)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in retained)
    assert all((path.parent / ".owner.json").stat().st_mode & 0o777 == 0o600 for path in retained)
    for path in retained:
        marker = json.loads((path.parent / ".owner.json").read_bytes())
        metadata = path.parent.stat()
        assert marker["device"] == metadata.st_dev
        assert marker["inode"] == metadata.st_ino


def test_postread_visible_mode_race_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, backup_root, work_root = _published_source(
        tmp_path, backup_id="visible-mode-race", inventory_update=None
    )
    database = inventory.source_root / "data/economy.db"
    original_fstat = recovery.os.fstat
    source_descriptor: int | None = None
    calls = 0

    def race_fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        metadata = original_fstat(descriptor)
        if descriptor == source_descriptor:
            calls += 1
            if calls == 2:
                database.chmod(0o600)
        return metadata

    with (
        pytest.raises(recovery.RecoveryError),
        recovery.open_backup_bound_source(
            inventory,
            backup_root=backup_root,
            backup_id="visible-mode-race",
            work_root=work_root,
        ) as source,
    ):
        source_descriptor = source._state.main_descriptor
        monkeypatch.setattr(recovery.os, "fstat", race_fstat)
        with pytest.raises(recovery.RecoveryError) as error:
            source.verify_before_write()
        assert error.value.reason == "source_changed"

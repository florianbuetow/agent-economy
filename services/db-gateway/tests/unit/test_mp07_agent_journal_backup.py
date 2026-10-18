"""T-181 backup and restore proofs for caller-owned agent journals."""

from __future__ import annotations

import hashlib
import json
import stat
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from service_commons.maintenance_lock import (
    MaintenanceLockMode,
    hold_existing_maintenance_lock,
    hold_maintenance_lock,
)
from service_commons.recovery_contract import (
    BindingKind,
    CaptureKind,
    OwnedProcessV1,
    PathResolutionBase,
    RecoveryInventoryV1,
    ResourceKind,
    SourceConfigV1,
    SourceFieldBindingV1,
    SourceResourceV1,
    TargetBindingsV1,
)

from db_gateway_service import recovery
from db_gateway_service.recovery import (
    PublicationState,
    RecoveryError,
    create_backup,
    restore_backup,
    verify_backup,
    verify_restore,
)
from tests.unit.test_mp06_recovery import _bindings as _base_bindings
from tests.unit.test_mp06_recovery import _source

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = pytest.mark.unit

_SOURCE_URLS = {
    "identity_url": "https://identity.source.example/v1",
    "bank_url": "https://bank.source.example/v1",
    "task_board_url": "https://task-board.source.example/v1",
    "reputation_url": "https://reputation.source.example/v1",
    "court_url": "https://court.source.example/v1",
}
_FINGERPRINTS = ("1" * 64, "2" * 64)


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write(path: Path, value: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value)
    path.chmod(mode)


def _owner_bytes(fingerprint: str) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "environment_id": "backup-test-environment",
            "role": "math-worker",
            "profile": "mathbot_openai",
            "handle": f"worker-{fingerprint[0]}",
            "registered_agent_id": f"a-worker-{fingerprint[0]}",
            "key_fingerprint": fingerprint,
            "service_urls": _SOURCE_URLS,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _intent_bytes(fingerprint: str) -> bytes:
    return json.dumps(
        {
            "stored_prerequisite": True,
            "task_id": f"t-00000000-0000-4000-8000-{int(fingerprint[0]):012d}",
            "unicode": "Résultat ∑",
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _create_identity(journal_root: Path, fingerprint: str) -> Path:
    identity = journal_root / "identities" / fingerprint
    intents = identity / "intents"
    intents.mkdir(parents=True, mode=0o700)
    (journal_root / "identities").chmod(0o700)
    identity.chmod(0o700)
    intents.chmod(0o700)
    _write(identity / "owner.json", _owner_bytes(fingerprint), mode=0o600)
    _write(intents / "intent-prerequisite.json", _intent_bytes(fingerprint), mode=0o600)
    lock_path = identity / "runtime.lock"
    with hold_maintenance_lock(
        lock_path,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass
    return identity


def _agents_config_bytes(*, directory: str = "../agent-state") -> bytes:
    return json.dumps(
        {
            "runtime_state": {
                "environment_id": "backup-test-environment",
                "directory": directory,
                "diagnostic_limit": 8,
                "max_record_bytes": 65_536,
            },
            "platform": dict(_SOURCE_URLS),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _journal_inventory(
    tmp_path: Path,
    *,
    capture_kind: CaptureKind,
    present: bool,
) -> tuple[RecoveryInventoryV1, sqlite3.Connection, Path]:
    inventory, connection = _source(tmp_path)
    source = inventory.source_root
    journal_root = source / "agent-state"
    if present:
        journal_root.mkdir(mode=0o700)
        for fingerprint in _FINGERPRINTS:
            _create_identity(journal_root, fingerprint)

    agents_config = source / "configs" / "agents.yaml"
    config_bytes = _agents_config_bytes()
    _write(agents_config, config_bytes, mode=0o600)
    agents_cwd = source / "agents-runtime"
    agents_cwd.mkdir(mode=0o700)

    resources = (
        *inventory.resources,
        SourceResourceV1(
            logical_id="config.agents",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            capture_root=source,
            captured_path=agents_config,
        ),
        SourceResourceV1(
            logical_id="agents.runtime_state",
            kind=ResourceKind.DIRECTORY,
            capture_kind=capture_kind,
            capture_root=source,
            captured_path=journal_root,
        ),
    )
    bindings: list[SourceFieldBindingV1] = [
        *inventory.field_bindings,
        SourceFieldBindingV1(
            binding_id="agents:runtime_state.directory",
            config_id="agents",
            field_path=("runtime_state", "directory"),
            binding_kind=BindingKind.FILESYSTEM,
            captured_value="../agent-state",
            resolution_base=PathResolutionBase.CONFIG_PARENT,
            resolved_source_path=journal_root,
            resource_id="agents.runtime_state",
        ),
    ]
    for field, value in _SOURCE_URLS.items():
        bindings.append(
            SourceFieldBindingV1(
                binding_id=f"agents:platform.{field}",
                config_id="agents",
                field_path=("platform", field),
                binding_kind=BindingKind.NETWORK,
                captured_value=value,
                resolution_base=None,
                resolved_source_path=None,
                resource_id=None,
            )
        )
    process_ownership = (
        *inventory.process_ownership,
        OwnedProcessV1(
            logical_id="agents",
            config_id="agents",
            process_cwd=agents_cwd,
            agents_package_root=inventory.agents_package_root,
            pid=None,
            pgid=None,
            sid=None,
            running=False,
            group_drained=False,
            leader_returncode=None,
            cleanup_error=None,
        ),
    )
    updated = inventory.model_copy(
        update={
            "resources": resources,
            "configs": (
                *inventory.configs,
                SourceConfigV1(
                    logical_id="agents",
                    capture_root=source,
                    captured_path=agents_config,
                    process_cwd=agents_cwd,
                    sha256=_digest(config_bytes),
                ),
            ),
            "field_bindings": tuple(bindings),
            "process_ownership": process_ownership,
        }
    )
    return updated, connection, journal_root


def _journal_bindings(
    inventory: RecoveryInventoryV1,
    target: Path,
    *,
    target_urls: dict[str, str] | None = None,
) -> TargetBindingsV1:
    base = _base_bindings(inventory, target)
    resource_paths = dict(base.resource_paths)
    resource_paths["config.agents"] = target / "configs" / "agents.yaml"
    resource_paths["agents.runtime_state"] = target / "agent-state"
    endpoints = dict(base.endpoint_values)
    for field, value in (target_urls or _SOURCE_URLS).items():
        endpoints[f"agents:platform.{field}"] = value
    return TargetBindingsV1(
        contract_version=1,
        target_root=target,
        process_cwds={**base.process_cwds, "agents": target / "agents-runtime"},
        agents_package_root=target / "agents",
        resource_paths=resource_paths,
        endpoint_values=endpoints,
    )


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    return backup_root, work_root


def _journal_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _journal_modes(root: Path) -> dict[str, int]:
    return {
        path.relative_to(root).as_posix(): stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_present_journal_bytes_restore_exactly_without_owner_rewriting(
    tmp_path: Path,
) -> None:
    inventory, connection, journal_root = _journal_inventory(
        tmp_path,
        capture_kind=CaptureKind.COPY,
        present=True,
    )
    source_bytes = _journal_files(journal_root)
    source_modes = _journal_modes(journal_root)
    backup_root, work_root = _roots(tmp_path)
    created = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="agent-journal",
        work_root=work_root,
    )
    assert created.state is PublicationState.COMPLETE
    assert verify_backup(
        backup_root=backup_root,
        backup_id="agent-journal",
        work_root=work_root,
    ).snapshot_preserved

    target = tmp_path / "target"
    bindings = _journal_bindings(inventory, target)
    restore_backup(
        backup_root=backup_root,
        backup_id="agent-journal",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    assert _journal_files(bindings.resource_paths["agents.runtime_state"]) == source_bytes
    assert _journal_modes(bindings.resource_paths["agents.runtime_state"]) == source_modes
    assert verify_restore(target_root=target, work_root=work_root).snapshot_preserved
    connection.close()


def test_missing_owner_is_preserved_but_marks_restored_identity_incomplete(
    tmp_path: Path,
) -> None:
    inventory, connection, journal_root = _journal_inventory(
        tmp_path,
        capture_kind=CaptureKind.COPY,
        present=True,
    )
    incomplete_identity = journal_root / "identities" / _FINGERPRINTS[0]
    (incomplete_identity / "owner.json").unlink()
    assert (incomplete_identity / "runtime.lock").is_file()
    assert any((incomplete_identity / "intents").iterdir())
    source_bytes = _journal_files(journal_root)
    source_modes = _journal_modes(journal_root)

    backup_root, work_root = _roots(tmp_path)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="missing-agent-owner",
        work_root=work_root,
    )
    target = tmp_path / "target"
    bindings = _journal_bindings(inventory, target)
    restore_backup(
        backup_root=backup_root,
        backup_id="missing-agent-owner",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    restored_root = bindings.resource_paths["agents.runtime_state"]
    restored_identity = restored_root / "identities" / _FINGERPRINTS[0]
    assert _journal_files(restored_root) == source_bytes
    assert _journal_modes(restored_root) == source_modes
    assert not (restored_identity / "owner.json").exists()
    assert (restored_identity / "runtime.lock").is_file()
    assert any((restored_identity / "intents").iterdir())

    verified = verify_restore(target_root=target, work_root=work_root)
    connection.close()
    assert verified.snapshot_preserved is True
    assert verified.identity_complete is False
    assert verified.operational_restore_verified is False
    assert "agent_runtime_owner_invalid" in verified.finding_codes


def test_present_journal_snapshot_rejects_busy_identity_lease(tmp_path: Path) -> None:
    inventory, connection, journal_root = _journal_inventory(
        tmp_path,
        capture_kind=CaptureKind.COPY,
        present=True,
    )
    backup_root, work_root = _roots(tmp_path)
    lock_path = journal_root / "identities" / _FINGERPRINTS[0] / "runtime.lock"
    metadata = lock_path.lstat()
    with (
        hold_existing_maintenance_lock(
            lock_path,
            mode=MaintenanceLockMode.EXCLUSIVE,
            blocking=False,
            expected_device=metadata.st_dev,
            expected_inode=metadata.st_ino,
        ),
        pytest.raises(RecoveryError) as raised,
    ):
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="busy-agent-journal",
            work_root=work_root,
        )
    assert raised.value.reason == "runtime_active"
    assert not (backup_root / "busy-agent-journal/COMPLETE").exists()
    connection.close()


def test_present_snapshot_holds_all_public_identity_leases_through_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory, connection, journal_root = _journal_inventory(
        tmp_path,
        capture_kind=CaptureKind.COPY,
        present=True,
    )
    backup_root, work_root = _roots(tmp_path)
    expected_locks = {
        journal_root / "identities" / fingerprint / "runtime.lock" for fingerprint in _FINGERPRINTS
    }
    active: set[Path] = set()
    observed: list[tuple[Path, MaintenanceLockMode, bool]] = []
    original_hold = recovery.hold_existing_maintenance_lock
    original_capture = recovery._capture_directory
    original_verify = recovery._verify_and_close_copy

    @contextmanager
    def tracked_hold(path: Path, **kwargs: Any) -> Iterator[object]:
        mode = kwargs["mode"]
        blocking = kwargs["blocking"]
        assert isinstance(mode, MaintenanceLockMode) and isinstance(blocking, bool)
        with original_hold(path, **kwargs) as held:
            if path in expected_locks:
                observed.append((path, mode, blocking))
                active.add(path)
            try:
                yield held
            finally:
                active.discard(path)

    def capture_directory(*args: object, **kwargs: object) -> object:
        if kwargs.get("source") == journal_root:
            assert active == expected_locks
        return original_capture(*args, **kwargs)

    def verify_copy(copied: Any) -> None:
        source_path = copied.source_path
        if source_path.is_relative_to(journal_root):
            assert active == expected_locks
        original_verify(copied)

    monkeypatch.setattr(recovery, "hold_existing_maintenance_lock", tracked_hold)
    monkeypatch.setattr(recovery, "_capture_directory", capture_directory)
    monkeypatch.setattr(recovery, "_verify_and_close_copy", verify_copy)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="leased-agent-journal",
        work_root=work_root,
    )
    assert observed == [
        (path, MaintenanceLockMode.EXCLUSIVE, False) for path in sorted(expected_locks)
    ]
    assert active == set()
    connection.close()


@pytest.mark.parametrize("mutation", ["new", "replaced", "disappeared"])
def test_identity_set_or_identity_replacement_during_snapshot_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    inventory, connection, journal_root = _journal_inventory(
        tmp_path,
        capture_kind=CaptureKind.COPY,
        present=True,
    )
    backup_root, work_root = _roots(tmp_path)
    original_capture = recovery._capture_directory
    changed = False
    expected_journal_bytes: dict[str, bytes] | None = None
    expected_journal_modes: dict[str, int] | None = None
    detached_path: Path | None = None
    expected_detached_bytes: dict[str, bytes] | None = None
    expected_detached_modes: dict[str, int] | None = None

    def mutate_after_capture(*args: object, **kwargs: object) -> object:
        nonlocal changed
        nonlocal detached_path, expected_detached_bytes, expected_detached_modes
        nonlocal expected_journal_bytes, expected_journal_modes
        result = original_capture(*args, **kwargs)
        if kwargs.get("source") != journal_root or changed:
            return result
        changed = True
        identity = journal_root / "identities" / _FINGERPRINTS[0]
        if mutation == "new":
            _create_identity(journal_root, "3" * 64)
        else:
            detached = journal_root.parent / f"detached-{mutation}"
            identity.rename(detached)
            detached_path = detached
            if mutation == "replaced":
                _create_identity(journal_root, _FINGERPRINTS[0])
            expected_detached_bytes = _journal_files(detached)
            expected_detached_modes = _journal_modes(detached)
        expected_journal_bytes = _journal_files(journal_root)
        expected_journal_modes = _journal_modes(journal_root)
        return result

    monkeypatch.setattr(recovery, "_capture_directory", mutate_after_capture)
    with pytest.raises(RecoveryError) as raised:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id=f"changed-{mutation}",
            work_root=work_root,
        )
    assert raised.value.reason in {"source_changed", "publication_failed"}
    assert not (backup_root / f"changed-{mutation}/COMPLETE").exists()
    assert expected_journal_bytes is not None
    assert expected_journal_modes is not None
    assert _journal_files(journal_root) == expected_journal_bytes
    assert _journal_modes(journal_root) == expected_journal_modes
    if detached_path is not None:
        assert expected_detached_bytes is not None
        assert expected_detached_modes is not None
        assert _journal_files(detached_path) == expected_detached_bytes
        assert _journal_modes(detached_path) == expected_detached_modes
    connection.close()


def test_absent_journal_stays_absent_after_restore(tmp_path: Path) -> None:
    absent_kind = getattr(CaptureKind, "ABSENT_DIRECTORY", None)
    assert absent_kind is not None, (
        "T-181 capability missing: CaptureKind.ABSENT_DIRECTORY is required"
    )
    inventory, connection, journal_root = _journal_inventory(
        tmp_path,
        capture_kind=absent_kind,
        present=False,
    )
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="absent-agent-journal",
        work_root=work_root,
    )
    manifest = json.loads((backup_root / "absent-agent-journal/payload/manifest.json").read_text())
    record = manifest["resources"]["agents.runtime_state"]
    assert record["capture_kind"] == "absent_directory"
    assert record["present"] is False
    assert "payload_path" not in record

    target = tmp_path / "target"
    bindings = _journal_bindings(inventory, target)
    restore_backup(
        backup_root=backup_root,
        backup_id="absent-agent-journal",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    assert not journal_root.exists()
    assert not bindings.resource_paths["agents.runtime_state"].exists()
    connection.close()


def test_changed_target_urls_preserve_snapshot_but_report_owner_binding_mismatch(
    tmp_path: Path,
) -> None:
    inventory, connection, journal_root = _journal_inventory(
        tmp_path,
        capture_kind=CaptureKind.COPY,
        present=True,
    )
    source_bytes = _journal_files(journal_root)
    source_modes = _journal_modes(journal_root)
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="retargeted-agent-journal",
        work_root=work_root,
    )
    target_urls = {
        **_SOURCE_URLS,
        "task_board_url": "https://task-board.target.example/v1",
    }
    target = tmp_path / "target"
    bindings = _journal_bindings(inventory, target, target_urls=target_urls)
    restore_backup(
        backup_root=backup_root,
        backup_id="retargeted-agent-journal",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    restored_root = bindings.resource_paths["agents.runtime_state"]
    assert _journal_files(restored_root) == source_bytes
    assert _journal_modes(restored_root) == source_modes
    restored_owner = json.loads(
        (restored_root / "identities" / _FINGERPRINTS[0] / "owner.json").read_text()
    )
    assert restored_owner["service_urls"] == _SOURCE_URLS
    active_config = json.loads(bindings.resource_paths["config.agents"].read_text())
    assert active_config["platform"] == target_urls

    verified = verify_restore(target_root=target, work_root=work_root)
    assert verified.snapshot_preserved is True
    assert verified.operational_restore_verified is False
    assert "agent_runtime_owner_binding_mismatch" in verified.finding_codes
    connection.close()

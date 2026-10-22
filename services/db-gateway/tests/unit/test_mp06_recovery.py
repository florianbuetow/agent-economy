"""Acceptance tests for the MP-06 immutable backup and fresh-target restore engine."""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import shutil
import sqlite3
from typing import TYPE_CHECKING

import pytest
from service_auth import generate_keypair
from service_auth.signing import keypair_fingerprint, load_public_key, public_key_to_b64
from service_commons.maintenance_lock import MaintenanceLockMode, hold_maintenance_lock
from service_commons.recovery_contract import (
    BindingKind,
    CapturedRegistrationState,
    CaptureKind,
    KeypairRole,
    MaintenanceLockIdentityV1,
    OwnedProcessV1,
    PathResolutionBase,
    RecoveryInventoryV1,
    RegistrationRequirement,
    ResourceKind,
    SourceConfigV1,
    SourceFieldBindingV1,
    SourceKeypairV1,
    SourceResourceV1,
    TargetBindingsV1,
)

from db_gateway_service import recovery
from db_gateway_service.recovery import (
    PublicationState,
    RecoveryError,
    create_backup,
    finalize_backup,
    restore_backup,
    verify_backup,
    verify_restore,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_public_key(path: Path) -> str:
    return f"ed25519:{public_key_to_b64(load_public_key(path))}"


def _write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def _source(tmp_path: Path) -> tuple[RecoveryInventoryV1, sqlite3.Connection]:
    source = tmp_path / "source"
    process_cwd = source / "gateway"
    process_cwd.mkdir(parents=True)
    database = source / "data" / "economy.db"
    database.parent.mkdir()
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE proof (value TEXT NOT NULL)")
    connection.commit()
    connection.execute("INSERT INTO proof VALUES ('committed-in-wal')")
    connection.commit()

    assets = source / "assets"
    asset = assets / "task-1" / "asset-1" / "answer.txt"
    _write(asset, b"forty-two", mode=0o640)
    connection.execute(
        "CREATE TABLE board_assets "
        "(asset_id TEXT, storage_path TEXT, size_bytes INTEGER, content_hash TEXT)"
    )
    connection.execute(
        "INSERT INTO board_assets VALUES (?, ?, ?, ?)",
        ("asset-1", str(asset), len(b"forty-two"), _digest(b"forty-two")),
    )
    connection.commit()
    empty_directory = source / "empty"
    empty_directory.mkdir()
    schema = source / "schema.sql"
    _write(schema, b"CREATE TABLE proof (value TEXT NOT NULL);\n", mode=0o644)
    tasks = source / "agents" / "tasks.json"
    _write(tasks, b'[{"question":"1+1"}]\n', mode=0o644)
    keys = source / "keys"
    keys.mkdir()
    generate_keypair("operator", keys)
    generate_keypair("gateway-service", keys)
    roster = source / "agents" / "roster.yaml"
    roster_bytes = b'{"agents":{"operator":{"type":"operator"}}}\n'
    _write(roster, roster_bytes, mode=0o640)

    config = source / "configs" / "gateway.yaml"
    config_bytes = json.dumps(
        {
            "database": {"path": "../data/economy.db"},
            "server": {"port": 8007},
            "user_agent": {"handle": "operator"},
            "data": {"roster_path": "../agents/roster.yaml"},
            "recovery": {"maintenance_lock_path": "../maintenance.lock"},
            "auth": {
                "trusted_callers": {
                    "gateway-service": {"public_key_path": "../keys/gateway-service.pub"}
                }
            },
        },
        sort_keys=True,
    ).encode()
    _write(config, config_bytes, mode=0o640)
    maintenance_lock = source / "maintenance.lock"
    with hold_maintenance_lock(
        maintenance_lock,
        mode=MaintenanceLockMode.EXCLUSIVE,
        blocking=False,
    ):
        pass
    maintenance_metadata = maintenance_lock.lstat()

    resources = (
        SourceResourceV1(
            logical_id="database.primary",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.COPY,
            captured_path=database,
            capture_root=database.parent,
        ),
        SourceResourceV1(
            logical_id="assets.root",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.COPY,
            captured_path=assets,
            capture_root=assets,
        ),
        SourceResourceV1(
            logical_id="database.schema",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.COPY,
            captured_path=schema,
            capture_root=source,
        ),
        SourceResourceV1(
            logical_id="agents.tasks",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.COPY,
            captured_path=tasks,
            capture_root=tasks.parent,
        ),
        SourceResourceV1(
            logical_id="runtime.empty",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.DIRECTORY_ONLY,
            captured_path=empty_directory,
            capture_root=empty_directory,
        ),
        SourceResourceV1(
            logical_id="config.gateway",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.CONFIG,
            captured_path=config,
            capture_root=config.parent,
        ),
        SourceResourceV1(
            logical_id="agents.roster",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.COPY,
            captured_path=roster,
            capture_root=roster.parent,
        ),
        SourceResourceV1(
            logical_id="runtime.maintenance_lock",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.RUNTIME_LOCK,
            captured_path=maintenance_lock,
            capture_root=source,
        ),
        SourceResourceV1(
            logical_id="key.operator.private",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            captured_path=keys / "operator.key",
            capture_root=keys,
        ),
        SourceResourceV1(
            logical_id="key.operator.public",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            captured_path=keys / "operator.pub",
            capture_root=keys,
        ),
        SourceResourceV1(
            logical_id="key.gateway-service.private",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            captured_path=keys / "gateway-service.key",
            capture_root=keys,
        ),
        SourceResourceV1(
            logical_id="key.gateway-service.public",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.KEYPAIR,
            captured_path=keys / "gateway-service.pub",
            capture_root=keys,
        ),
    )
    inventory = RecoveryInventoryV1(
        contract_version=1,
        runtime_id="mp06-test",
        source_root=source,
        agents_package_root=source / "agents",
        maintenance_lock_path=maintenance_lock,
        maintenance_lock_identity=MaintenanceLockIdentityV1(
            device=maintenance_metadata.st_dev,
            inode=maintenance_metadata.st_ino,
        ),
        resources=resources,
        configs=(
            SourceConfigV1(
                logical_id="gateway",
                captured_path=config,
                capture_root=config.parent,
                process_cwd=process_cwd,
                sha256=_digest(config_bytes),
            ),
        ),
        field_bindings=(
            SourceFieldBindingV1(
                binding_id="gateway.database.path",
                config_id="gateway",
                field_path=("database", "path"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value="../data/economy.db",
                resolution_base=PathResolutionBase.PROCESS_CWD,
                resolved_source_path=database,
                resource_id="database.primary",
            ),
            SourceFieldBindingV1(
                binding_id="gateway.server.port",
                config_id="gateway",
                field_path=("server", "port"),
                binding_kind=BindingKind.PORT,
                captured_value=8007,
                resolution_base=None,
                resolved_source_path=None,
                resource_id=None,
            ),
            SourceFieldBindingV1(
                binding_id="gateway.data.roster_path",
                config_id="gateway",
                field_path=("data", "roster_path"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value="../agents/roster.yaml",
                resolution_base=PathResolutionBase.PROCESS_CWD,
                resolved_source_path=roster,
                resource_id="agents.roster",
            ),
            SourceFieldBindingV1(
                binding_id="gateway.auth.gateway-service.public_key_path",
                config_id="gateway",
                field_path=(
                    "auth",
                    "trusted_callers",
                    "gateway-service",
                    "public_key_path",
                ),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value="../keys/gateway-service.pub",
                resolution_base=PathResolutionBase.CONFIG_PARENT,
                resolved_source_path=keys / "gateway-service.pub",
                resource_id="key.gateway-service.public",
            ),
            SourceFieldBindingV1(
                binding_id="gateway.recovery.maintenance_lock_path",
                config_id="gateway",
                field_path=("recovery", "maintenance_lock_path"),
                binding_kind=BindingKind.FILESYSTEM,
                captured_value="../maintenance.lock",
                resolution_base=PathResolutionBase.CONFIG_PARENT,
                resolved_source_path=maintenance_lock,
                resource_id="runtime.maintenance_lock",
            ),
        ),
        keypairs=(
            SourceKeypairV1(
                handle="operator",
                role=KeypairRole.ECONOMIC,
                private_resource_id="key.operator.private",
                public_resource_id="key.operator.public",
                expected_public_fingerprint_sha256=keypair_fingerprint(
                    "operator", keys, allow_legacy_private_mode=False
                ),
                registration_requirement=RegistrationRequirement.OPTIONAL,
                captured_registration_state=CapturedRegistrationState.UNREGISTERED,
                expected_registered_agent_id=None,
            ),
            SourceKeypairV1(
                handle="gateway-service",
                role=KeypairRole.GATEWAY,
                private_resource_id="key.gateway-service.private",
                public_resource_id="key.gateway-service.public",
                expected_public_fingerprint_sha256=keypair_fingerprint(
                    "gateway-service", keys, allow_legacy_private_mode=False
                ),
                registration_requirement=RegistrationRequirement.FORBIDDEN,
                captured_registration_state=CapturedRegistrationState.UNREGISTERED,
                expected_registered_agent_id=None,
            ),
        ),
        process_ownership=(
            OwnedProcessV1(
                logical_id="gateway",
                config_id="gateway",
                process_cwd=process_cwd,
                agents_package_root=None,
                pid=None,
                pgid=None,
                sid=None,
                running=False,
                group_drained=False,
                leader_returncode=None,
                cleanup_error=None,
            ),
        ),
    )
    return inventory, connection


def _bindings(inventory: RecoveryInventoryV1, target: Path) -> TargetBindingsV1:
    resource_paths = {
        resource.logical_id: target / "resources" / resource.logical_id
        for resource in inventory.resources
    }
    resource_paths["database.primary"] = target / "data" / "economy.db"
    resource_paths["assets.root"] = target / "assets"
    resource_paths["config.gateway"] = target / "configs" / "gateway.yaml"
    return TargetBindingsV1(
        contract_version=1,
        target_root=target,
        process_cwds={"gateway": target / "gateway"},
        agents_package_root=target / "agents",
        resource_paths=resource_paths,
        endpoint_values={"gateway.server.port": 18007},
    )


def test_committed_wal_data_and_all_capture_intents_survive_fresh_restore(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    (inventory.source_root / "assets/empty/deep").mkdir(parents=True)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    created = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="backup-001",
        work_root=work_root,
    )
    assert created.state is PublicationState.COMPLETE
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="backup-001",
        work_root=work_root,
    )
    assert verified.snapshot_preserved is True
    assert verified.identity_complete is True
    manifest = json.loads((backup_root / "backup-001/payload/manifest.json").read_text())
    database_record = manifest["resources"]["database.primary"]
    assert database_record["source_identity"]["sha256"] == _digest(
        (backup_root / "backup-001/payload/database/economy.db").read_bytes()
    )
    assert database_record["wal_source_identity"]["present"] is True
    assert manifest["audit_sha256"] == _digest(
        (backup_root / "backup-001/payload/audits/pre-migration.json").read_bytes()
    )
    assert not (backup_root / "backup-001/payload/database/economy.db-shm").exists()

    source_public = (inventory.source_root / "keys/operator.pub").read_bytes()
    connection.close()
    shutil.rmtree(inventory.source_root)
    target = tmp_path / "restored"
    restored = restore_backup(
        backup_root=backup_root,
        backup_id="backup-001",
        target_bindings=_bindings(inventory, target),
        target_root=target,
        work_root=work_root,
    )
    assert restored.state is PublicationState.COMPLETE
    restore_verification = verify_restore(target_root=target, work_root=work_root)
    assert restore_verification.snapshot_preserved is True
    assert restore_verification.identity_complete is True
    assert restore_verification.migration_eligible is False
    assert restore_verification.operational_restore_verified is False
    assert "unknown_schema" in restore_verification.finding_codes
    restore_manifest = json.loads((target / "restore-manifest.json").read_text())
    assert restore_manifest["source_inventory"]["runtime_id"] == inventory.runtime_id
    assert restore_manifest["source_verification"]["identity_complete"] is True
    assert restore_manifest["source_audit_sha256"] == _digest(
        json.dumps(
            restore_manifest["source_audit"],
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    assert restore_manifest["target_audit_sha256"] == _digest(
        json.dumps(
            restore_manifest["target_audit"],
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    )
    assert restore_manifest["expected_files"]
    assert restore_manifest["expected_directories"]
    assert restore_manifest["target_maintenance_lock"]["mode"] == 0o600
    restored_db = target / "data" / "economy.db"
    with sqlite3.connect(restored_db) as restored_connection:
        assert restored_connection.execute("SELECT value FROM proof").fetchall() == [
            ("committed-in-wal",)
        ]
    assert (target / "assets/task-1/asset-1/answer.txt").read_bytes() == b"forty-two"
    assert (target / "assets/empty/deep").is_dir()
    assert (target / "resources/agents.tasks").read_bytes().startswith(b"[")
    assert (target / "resources/runtime.empty").is_dir()
    assert not any((target / "resources/runtime.empty").iterdir())
    assert (target / "resources/key.operator.private").stat().st_mode & 0o777 == 0o600
    assert (target / "resources/key.operator.public").read_bytes() == source_public
    rendered = json.loads((target / "configs/gateway.yaml").read_text())
    assert rendered["database"]["path"] == "../data/economy.db"
    assert rendered["server"]["port"] == 18007


def test_public_results_serialize_distinct_recovery_states(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    publication = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="serialized",
        work_root=work_root,
    )
    verification = verify_backup(
        backup_root=backup_root,
        backup_id="serialized",
        work_root=work_root,
    )

    assert publication.model_dump(mode="json")["state"] == "complete"
    assert verification.model_dump(mode="json") == {
        "artifact_verified": True,
        "snapshot_preserved": True,
        "identity_complete": True,
        "migration_eligible": False,
        "operational_restore_verified": False,
        "posting_recovery": {
            "applicability": "not_applicable",
            "operational_ready": True,
            "ledgers": [],
        },
        "finding_codes": ["unknown_schema"],
        "manifest_sha256": verification.manifest_sha256,
    }
    connection.close()


def test_restore_never_publishes_undeclared_content_or_changed_rendered_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="target-evidence",
        work_root=work_root,
    )
    target = tmp_path / "target"
    original_render = recovery._render_configs

    def render_then_add_undeclared(*args: object, **kwargs: object) -> object:
        changes = original_render(*args, **kwargs)
        (target / "resources/undeclared.txt").write_text("not declared")
        active_config = target / "configs/gateway.yaml"
        active_config.write_text(active_config.read_text() + "\n")
        return changes

    monkeypatch.setattr(recovery, "_render_configs", render_then_add_undeclared)
    with pytest.raises(RecoveryError) as error:
        restore_backup(
            backup_root=backup_root,
            backup_id="target-evidence",
            target_bindings=_bindings(inventory, target),
            target_root=target,
            work_root=work_root,
        )
    assert error.value.reason in {"digest_mismatch", "target_mismatch", "invalid_manifest"}
    assert not (target / "COMPLETE").exists()
    connection.close()


def test_verify_restore_requires_exclusive_recorded_target_lock(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="target-lock",
        work_root=work_root,
    )
    target = tmp_path / "target"
    bindings = _bindings(inventory, target)
    restore_backup(
        backup_root=backup_root,
        backup_id="target-lock",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    target_lock = bindings.resource_paths["runtime.maintenance_lock"]
    with (
        hold_maintenance_lock(
            target_lock,
            mode=MaintenanceLockMode.SHARED,
            blocking=False,
        ),
        pytest.raises(RecoveryError) as error,
    ):
        verify_restore(target_root=target, work_root=work_root)
    assert error.value.reason == "runtime_active"
    connection.close()


def test_verify_restore_parses_only_authenticated_manifest_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="single-manifest-read",
        work_root=work_root,
    )
    target = tmp_path / "target"
    restore_backup(
        backup_root=backup_root,
        backup_id="single-manifest-read",
        target_bindings=_bindings(inventory, target),
        target_root=target,
        work_root=work_root,
    )
    original = recovery._read_json_regular
    json_reads: list[Path] = []

    def record_json_read(path: Path, *, missing_reason: str) -> dict[str, object]:
        json_reads.append(path)
        return original(path, missing_reason=missing_reason)

    monkeypatch.setattr(recovery, "_read_json_regular", record_json_read)
    assert verify_restore(target_root=target, work_root=work_root).artifact_verified
    assert target / "restore-manifest.json" not in json_reads
    connection.close()


def test_new_file_creation_cannot_escape_after_ancestor_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "fresh"
    parent = root / "nested"
    parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = parent / "result.txt"
    displaced = root / "displaced"
    real_open = recovery.os.open
    swapped = False

    def racing_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        is_final = path in (target, target.name)
        if is_final and flags & recovery.os.O_CREAT and not swapped:
            swapped = True
            parent.rename(displaced)
            parent.symlink_to(outside, target_is_directory=True)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(recovery.os, "open", racing_open)
    with pytest.raises(RecoveryError):
        recovery.write_new_recovery_file(target, b"safe", mode=0o600)
    assert swapped
    assert not (outside / "result.txt").exists()


def test_cleanup_failures_preserve_primary_write_and_fsync_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written_descriptor: int | None = None
    real_close = recovery.os.close

    def fail_write(descriptor: int, _data: object) -> int:
        nonlocal written_descriptor
        written_descriptor = descriptor
        raise OSError(errno.ENOSPC, "write-primary")

    def fail_written_close(descriptor: int) -> None:
        if descriptor == written_descriptor:
            raise OSError("write-close-secondary")
        real_close(descriptor)

    monkeypatch.setattr(recovery.os, "write", fail_write)
    monkeypatch.setattr(recovery.os, "close", fail_written_close)
    with pytest.raises(RecoveryError) as write_error:
        recovery.write_new_recovery_file(tmp_path / "output.bin", b"data", mode=0o600)
    assert write_error.value.reason == "write_failed"
    assert any("write-close-secondary" in note for note in write_error.value.__notes__)

    monkeypatch.undo()
    synced_descriptor: int | None = None
    real_close = recovery.os.close

    def fail_fsync(descriptor: int) -> None:
        nonlocal synced_descriptor
        synced_descriptor = descriptor
        raise OSError("fsync-primary")

    def fail_synced_close(descriptor: int) -> None:
        if descriptor == synced_descriptor:
            raise OSError("fsync-close-secondary")
        real_close(descriptor)

    monkeypatch.setattr(recovery.os, "fsync", fail_fsync)
    monkeypatch.setattr(recovery.os, "close", fail_synced_close)
    with pytest.raises(OSError, match="fsync-primary") as fsync_error:
        recovery._fsync_directory(tmp_path)
    assert any("fsync-close-secondary" in note for note in fsync_error.value.__notes__)


def test_partial_or_missing_key_inventory_preserves_snapshot_but_blocks_restore(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    partial = inventory.model_copy(update={"keypairs": ()})
    create_backup(
        partial,
        backup_root=backup_root,
        backup_id="partial-identity",
        work_root=work_root,
    )
    partial_result = verify_backup(
        backup_root=backup_root,
        backup_id="partial-identity",
        work_root=work_root,
    )
    assert partial_result.snapshot_preserved is True
    assert partial_result.identity_complete is False
    assert "missing_keypair_inventory" in partial_result.finding_codes
    target = tmp_path / "partial-target"
    with pytest.raises(RecoveryError) as restore_error:
        restore_backup(
            backup_root=backup_root,
            backup_id="partial-identity",
            target_bindings=_bindings(partial, target),
            target_root=target,
            work_root=work_root,
        )
    assert restore_error.value.reason == "restore_ineligible"

    key = inventory.source_root / "keys/operator.key"
    key.unlink()
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="missing-key",
        work_root=work_root,
    )
    missing_result = verify_backup(
        backup_root=backup_root,
        backup_id="missing-key",
        work_root=work_root,
    )
    assert missing_result.snapshot_preserved is True
    assert missing_result.identity_complete is False
    assert "missing_key_material" in missing_result.finding_codes
    connection.close()


def test_backup_preserves_legacy_money_bytes_and_records_anomaly(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    connection.execute("CREATE TABLE bank_accounts (account_id TEXT, balance REAL)")
    connection.execute("INSERT INTO bank_accounts VALUES ('legacy', 1.25)")
    connection.commit()
    source_identity = _digest((inventory.source_root / "data/economy.db-wal").read_bytes())
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    result = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="legacy",
        work_root=work_root,
    )

    assert result.state is PublicationState.COMPLETE
    verification = verify_backup(
        backup_root=backup_root,
        backup_id="legacy",
        work_root=work_root,
    )
    assert verification.snapshot_preserved is True
    assert verification.migration_eligible is False
    assert "legacy_money_storage" in verification.finding_codes
    assert _digest((backup_root / "legacy/payload/database/economy.db-wal").read_bytes()) == (
        source_identity
    )
    connection.close()


def test_asset_anomalies_are_recorded_without_rewriting_preserved_rows(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    asset = inventory.source_root / "assets/task-1/asset-1/answer.txt"
    asset.write_bytes(b"changed")
    orphan = inventory.source_root / "assets/orphan.txt"
    orphan.write_bytes(b"orphan")
    missing = inventory.source_root / "assets/task-2/asset-2/missing.txt"
    connection.execute(
        "INSERT INTO board_assets VALUES (?, ?, ?, ?)",
        ("asset-2", str(missing), 5, _digest(b"never")),
    )
    connection.execute(
        "INSERT INTO board_assets VALUES (?, ?, ?, ?)",
        ("asset-3", str(tmp_path / "outside.txt"), 1, _digest(b"x")),
    )
    connection.commit()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="asset-findings",
        work_root=work_root,
    )
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="asset-findings",
        work_root=work_root,
    )

    assert {
        "asset_hash_mismatch",
        "asset_size_mismatch",
        "missing_asset",
        "orphaned_asset",
        "unsafe_asset_reference",
    }.issubset(verified.finding_codes)
    assert (
        backup_root / "asset-findings/payload/assets/task-1/asset-1/answer.txt"
    ).read_bytes() == b"changed"
    connection.close()


def test_backup_rejects_missing_or_symlinked_declared_resources(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    missing = inventory.source_root / "schema.sql"
    missing.unlink()
    with pytest.raises(RecoveryError, match=r"database\.schema") as missing_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="missing",
            work_root=work_root,
        )
    assert missing_error.value.reason == "missing_resource"

    missing.write_text("schema")
    missing.unlink()
    missing.symlink_to(inventory.source_root / "agents/tasks.json")
    with pytest.raises(RecoveryError, match=r"database\.schema") as unsafe_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="unsafe",
            work_root=work_root,
        )
    assert unsafe_error.value.reason == "unsafe_resource"
    connection.close()


def test_source_inode_change_during_copy_aborts_without_complete_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    original = recovery._copy_open_file
    changed = False

    def replacing_copy(*args: object, **kwargs: object) -> object:
        nonlocal changed
        result = original(*args, **kwargs)
        source_path = kwargs.get("source_path")
        if source_path == inventory.source_root / "data/economy.db" and not changed:
            changed = True
            replacement = inventory.source_root / "data/replacement.db"
            replacement.write_bytes(b"replacement")
            replacement.replace(source_path)
        return result

    monkeypatch.setattr(recovery, "_copy_open_file", replacing_copy)
    with pytest.raises(RecoveryError) as error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="raced",
            work_root=work_root,
        )
    assert error.value.reason == "source_changed"
    assert not (backup_root / "raced/COMPLETE").exists()
    connection.close()


def test_wal_inode_swap_or_presence_change_aborts_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    original = recovery._copy_open_file
    wal = inventory.source_root / "data/economy.db-wal"

    def swapping_wal(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        if kwargs.get("source_path") == wal:
            replacement = wal.with_name("replacement.wal")
            replacement.write_bytes(wal.read_bytes())
            replacement.replace(wal)
        return result

    monkeypatch.setattr(recovery, "_copy_open_file", swapping_wal)
    with pytest.raises(RecoveryError) as swapped:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="wal-swap",
            work_root=work_root,
        )
    assert swapped.value.reason == "source_changed"
    assert not (backup_root / "wal-swap/COMPLETE").exists()
    connection.close()

    inventory, connection = _source(tmp_path / "presence")
    wal = inventory.source_root / "data/economy.db-wal"

    def removing_wal(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        if kwargs.get("source_path") == inventory.source_root / "data/economy.db":
            wal.unlink()
        return result

    monkeypatch.setattr(recovery, "_copy_open_file", removing_wal)
    with pytest.raises(RecoveryError) as disappeared:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="wal-disappeared",
            work_root=work_root,
        )
    assert disappeared.value.reason == "source_changed"
    assert not (backup_root / "wal-disappeared/COMPLETE").exists()
    connection.close()


def test_corrupt_or_incomplete_bundle_fails_closed(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="corrupt",
        work_root=work_root,
    )
    database = backup_root / "corrupt/payload/database/economy.db"
    database.write_bytes(database.read_bytes() + b"tamper")
    with pytest.raises(RecoveryError) as corrupted:
        verify_backup(
            backup_root=backup_root,
            backup_id="corrupt",
            work_root=work_root,
        )
    assert corrupted.value.reason == "digest_mismatch"

    (backup_root / "reserved").mkdir(mode=0o700)
    with pytest.raises(RecoveryError) as incomplete:
        verify_backup(
            backup_root=backup_root,
            backup_id="reserved",
            work_root=work_root,
        )
    assert incomplete.value.reason == "incomplete_bundle"
    connection.close()


def test_backup_and_restore_root_mode_tampering_is_rejected_without_chmod(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o755)
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="root-modes",
        work_root=work_root,
    )
    artifact_root = backup_root / "root-modes"
    payload_root = artifact_root / "payload"
    artifact_root.chmod(0o755)
    with pytest.raises(RecoveryError) as artifact_error:
        verify_backup(
            backup_root=backup_root,
            backup_id="root-modes",
            work_root=work_root,
        )
    assert artifact_error.value.reason == "unsafe_mode"
    assert artifact_root.stat().st_mode & 0o777 == 0o755
    artifact_root.chmod(0o700)
    payload_root.chmod(0o755)
    with pytest.raises(RecoveryError) as payload_error:
        verify_backup(
            backup_root=backup_root,
            backup_id="root-modes",
            work_root=work_root,
        )
    assert payload_error.value.reason == "unsafe_mode"
    assert payload_root.stat().st_mode & 0o777 == 0o755
    payload_root.chmod(0o700)

    target = tmp_path / "target"
    restore_backup(
        backup_root=backup_root,
        backup_id="root-modes",
        target_bindings=_bindings(inventory, target),
        target_root=target,
        work_root=work_root,
    )
    manifest_before = (target / "restore-manifest.json").read_bytes()
    target.chmod(0o755)
    with pytest.raises(RecoveryError) as verify_error:
        verify_restore(target_root=target, work_root=work_root)
    assert verify_error.value.reason == "unsafe_mode"
    with pytest.raises(RecoveryError) as finalize_error:
        recovery.finalize_restore(target_root=target, work_root=work_root)
    assert finalize_error.value.reason == "unsafe_mode"
    assert target.stat().st_mode & 0o777 == 0o755
    assert (target / "restore-manifest.json").read_bytes() == manifest_before
    connection.close()


@pytest.mark.parametrize(
    "tamper",
    [
        "resource_kind",
        "resource_capture_kind",
        "resource_source",
        "config_id",
        "config_sha",
        "key_mode",
        "db_identity",
        "wal_presence",
    ],
)
def test_rehashed_inner_manifest_contract_tampering_is_rejected(
    tmp_path: Path,
    tamper: str,
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="inner-contract",
        work_root=work_root,
    )
    artifact_root = backup_root / "inner-contract"
    payload = artifact_root / "payload"
    manifest_path = payload / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if tamper == "resource_kind":
        manifest["resources"]["database.schema"]["kind"] = "directory"
    elif tamper == "resource_capture_kind":
        manifest["resources"]["database.schema"]["capture_kind"] = "config"
    elif tamper == "resource_source":
        manifest["resources"]["assets.root"]["source_path"] = str(tmp_path / "other-assets")
    elif tamper == "config_id":
        manifest["resources"]["config.gateway"]["config_id"] = "different-config"
    elif tamper == "config_sha":
        manifest["inventory"]["configs"][0]["sha256"] = "0" * 64
    elif tamper == "key_mode":
        manifest["keypairs"]["operator"]["private_restore_mode"] = 0o644
    elif tamper == "db_identity":
        manifest["resources"]["database.primary"]["source_identity"]["sha256"] = "0" * 64
    else:
        manifest["resources"]["database.primary"]["wal_source_identity"] = {"present": False}
    manifest_bytes = json.dumps(
        manifest,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    manifest_path.write_bytes(manifest_bytes)
    manifest_digest = _digest(manifest_bytes)
    (payload / "manifest.sha256").write_text(f"{manifest_digest}\n")
    marker_path = artifact_root / "COMPLETE"
    marker = json.loads(marker_path.read_text())
    marker["manifest_sha256"] = manifest_digest
    marker["payload_sha256"] = recovery._tree_digest(payload, exclude=frozenset())
    marker_path.write_text(
        json.dumps(
            marker,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )

    with pytest.raises(RecoveryError) as error:
        verify_backup(
            backup_root=backup_root,
            backup_id="inner-contract",
            work_root=work_root,
        )
    assert error.value.reason in {
        "config_changed",
        "invalid_manifest",
        "unsafe_mode",
    }
    connection.close()


def test_unknown_bundle_format_is_rejected(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="format",
        work_root=work_root,
    )
    marker = backup_root / "format/COMPLETE"
    payload = json.loads(marker.read_text())
    payload["bundle_format_version"] = 999
    marker.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    with pytest.raises(RecoveryError) as error:
        verify_backup(
            backup_root=backup_root,
            backup_id="format",
            work_root=work_root,
        )
    assert error.value.reason == "unknown_format"
    connection.close()


def test_backup_and_restore_reservations_never_clobber_existing_paths(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    existing_backup = backup_root / "same"
    existing_backup.mkdir()
    sentinel = existing_backup / "sentinel"
    sentinel.write_text("keep")
    with pytest.raises(RecoveryError) as backup_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="same",
            work_root=work_root,
        )
    assert backup_error.value.reason == "already_exists"
    assert sentinel.read_text() == "keep"

    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="good",
        work_root=work_root,
    )
    target = tmp_path / "target"
    target.mkdir()
    target_sentinel = target / "sentinel"
    target_sentinel.write_text("keep")
    with pytest.raises(RecoveryError) as restore_error:
        restore_backup(
            backup_root=backup_root,
            backup_id="good",
            target_bindings=_bindings(inventory, target),
            target_root=target,
            work_root=work_root,
        )
    assert restore_error.value.reason == "already_exists"
    assert target_sentinel.read_text() == "keep"
    connection.close()


def test_overlap_and_incomplete_target_bindings_are_rejected(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    overlapping_backup_root = inventory.source_root / "assets/backups"
    overlapping_backup_root.mkdir()
    with pytest.raises(RecoveryError) as overlap:
        create_backup(
            inventory,
            backup_root=overlapping_backup_root,
            backup_id="overlap",
            work_root=tmp_path / "work",
        )
    assert overlap.value.reason == "path_overlap"

    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="binding",
        work_root=work_root,
    )
    target = tmp_path / "target"
    bindings = _bindings(inventory, target)
    incomplete = bindings.model_copy(
        update={
            "resource_paths": {
                key: value for key, value in bindings.resource_paths.items() if key != "assets.root"
            }
        }
    )
    with pytest.raises(RecoveryError) as error:
        restore_backup(
            backup_root=backup_root,
            backup_id="binding",
            target_bindings=incomplete,
            target_root=target,
            work_root=work_root,
        )
    assert error.value.reason == "incomplete_bindings"
    connection.close()


def test_operation_roots_reject_noncanonical_absolute_paths_before_io(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    noncanonical_work = tmp_path / "work-parent" / ".." / "work"
    with pytest.raises(RecoveryError) as create_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="noncanonical-create",
            work_root=noncanonical_work,
        )
    assert create_error.value.reason == "invalid_argument"
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="canonical-source",
        work_root=work_root,
    )
    with pytest.raises(RecoveryError) as verify_error:
        verify_backup(
            backup_root=backup_root / "child" / "..",
            backup_id="canonical-source",
            work_root=work_root,
        )
    assert verify_error.value.reason == "invalid_argument"
    target = tmp_path / "target"
    with pytest.raises(RecoveryError) as restore_error:
        restore_backup(
            backup_root=backup_root,
            backup_id="canonical-source",
            target_bindings=_bindings(inventory, target),
            target_root=tmp_path / "target-parent" / ".." / "target",
            work_root=work_root,
        )
    assert restore_error.value.reason == "invalid_argument"
    assert not target.exists()
    connection.close()


def test_verify_work_root_cannot_overlap_sibling_backup_artifact(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    for backup_id in ("first", "second"):
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id=backup_id,
            work_root=work_root,
        )
    second_payload = backup_root / "second/payload"
    digest_before = recovery._tree_digest(second_payload, exclude=frozenset())
    with pytest.raises(RecoveryError) as error:
        verify_backup(
            backup_root=backup_root,
            backup_id="first",
            work_root=second_payload,
        )
    assert error.value.reason == "path_overlap"
    assert recovery._tree_digest(second_payload, exclude=frozenset()) == digest_before
    connection.close()


def test_internal_backup_id_namespace_is_rejected_without_mutation(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    with pytest.raises(RecoveryError) as error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id=".attempts",
            work_root=work_root,
        )
    assert error.value.reason == "invalid_argument"
    assert not any(backup_root.iterdir())
    connection.close()


def test_backup_ancestor_swap_cannot_redirect_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    displaced = tmp_path / "displaced-backups"
    original_create = recovery._create_pinned_child
    swapped = False

    def create_then_swap(parent: object, name: str, *, mode: int) -> object:
        nonlocal swapped
        result = original_create(parent, name, mode=mode)
        if name == "ancestor-race" and not swapped:
            swapped = True
            backup_root.rename(displaced)
            backup_root.mkdir()
        return result

    monkeypatch.setattr(recovery, "_create_pinned_child", create_then_swap)
    with pytest.raises(RecoveryError):
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="ancestor-race",
            work_root=work_root,
        )
    assert swapped
    assert not any(backup_root.iterdir())
    assert not (backup_root / "ancestor-race/COMPLETE").exists()
    connection.close()


def test_backup_root_may_be_inside_provenance_container_when_disjoint_from_resources(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    external = tmp_path / "repository-inputs"
    external.mkdir()
    schema = external / "schema.sql"
    schema.write_text("CREATE TABLE proof (value TEXT NOT NULL);\n")
    web_root = external / "ui-web"
    web_root.mkdir()
    (web_root / "index.html").write_text("<main>captured UI</main>")
    backup_root = inventory.source_root / "backups"
    backup_root.mkdir()
    resources = (
        *(
            SourceResourceV1(
                logical_id=resource.logical_id,
                kind=resource.kind,
                capture_kind=resource.capture_kind,
                captured_path=(
                    schema if resource.logical_id == "database.schema" else resource.captured_path
                ),
                capture_root=(
                    external if resource.logical_id == "database.schema" else resource.capture_root
                ),
            )
            for resource in inventory.resources
        ),
        SourceResourceV1(
            logical_id="ui.webroot",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.COPY,
            captured_path=web_root,
            capture_root=web_root,
        ),
        SourceResourceV1(
            logical_id="recovery.backups",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.DIRECTORY_ONLY,
            captured_path=backup_root,
            capture_root=backup_root,
        ),
    )
    inventory = inventory.model_copy(update={"resources": resources})
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    result = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="runtime-shaped",
        work_root=work_root,
    )

    assert result.state is PublicationState.COMPLETE
    assert (
        backup_root / "runtime-shaped/payload/resources/database.schema/schema.sql"
    ).read_text() == schema.read_text()
    assert (
        backup_root / "runtime-shaped/payload/resources/ui.webroot/ui-web/index.html"
    ).read_text() == "<main>captured UI</main>"
    connection.close()


def test_capture_root_must_be_real_and_is_rechecked_after_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    linked_root = tmp_path / "linked-code"
    linked_root.symlink_to(inventory.source_root, target_is_directory=True)
    linked_schema = linked_root / "schema.sql"
    resources = tuple(
        resource.model_copy(update={"captured_path": linked_schema, "capture_root": linked_root})
        if resource.logical_id == "database.schema"
        else resource
        for resource in inventory.resources
    )
    linked_inventory = inventory.model_copy(update={"resources": resources})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    with pytest.raises(RecoveryError) as linked_error:
        create_backup(
            linked_inventory,
            backup_root=backup_root,
            backup_id="linked-root",
            work_root=work_root,
        )
    assert linked_error.value.reason == "unsafe_path"
    assert not (backup_root / "linked-root").exists()

    original_capture = recovery._capture_payload
    assets = inventory.source_root / "assets"

    def replace_root_after_capture(*args: object, **kwargs: object) -> object:
        result = original_capture(*args, **kwargs)
        displaced = inventory.source_root / "assets-displaced"
        assets.rename(displaced)
        assets.mkdir()
        return result

    monkeypatch.setattr(recovery, "_capture_payload", replace_root_after_capture)
    with pytest.raises(RecoveryError) as changed_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="changed-root",
            work_root=work_root,
        )
    assert changed_error.value.reason == "source_changed"
    assert not (backup_root / "changed-root/COMPLETE").exists()
    connection.close()


def test_directory_only_target_parent_may_contain_declared_child_resources(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    resources = tuple(
        SourceResourceV1(
            logical_id="keys.root",
            kind=ResourceKind.DIRECTORY,
            capture_kind=CaptureKind.DIRECTORY_ONLY,
            captured_path=inventory.source_root / "keys",
            capture_root=inventory.source_root / "keys",
        )
        if resource.logical_id == "runtime.empty"
        else resource
        for resource in inventory.resources
    )
    inventory = inventory.model_copy(update={"resources": resources})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="nested-targets",
        work_root=work_root,
    )
    target = tmp_path / "target"
    base_bindings = _bindings(inventory, target)
    resource_paths = dict(base_bindings.resource_paths)
    resource_paths["keys.root"] = target / "keys"
    for keypair in inventory.keypairs:
        resource_paths[keypair.private_resource_id] = target / "keys" / f"{keypair.handle}.key"
        resource_paths[keypair.public_resource_id] = target / "keys" / f"{keypair.handle}.pub"
    bindings = TargetBindingsV1(
        contract_version=1,
        target_root=target,
        process_cwds=dict(base_bindings.process_cwds),
        agents_package_root=base_bindings.agents_package_root,
        resource_paths=resource_paths,
        endpoint_values=dict(base_bindings.endpoint_values),
    )

    result = restore_backup(
        backup_root=backup_root,
        backup_id="nested-targets",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )

    assert result.state is PublicationState.COMPLETE
    assert (target / "keys/operator.key").stat().st_mode & 0o777 == 0o600
    assert (target / "keys/gateway-service.pub").is_file()
    assert verify_restore(target_root=target, work_root=work_root).identity_complete is True
    connection.close()


def test_operation_roots_are_rejected_before_mutating_artifacts(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_inside_assets = inventory.source_root / "assets/work"
    work_inside_assets.mkdir()
    with pytest.raises(RecoveryError) as create_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="bad-work",
            work_root=work_inside_assets,
        )
    assert create_error.value.reason == "path_overlap"
    assert not (backup_root / "bad-work").exists()
    provenance_work = inventory.source_root / "scratch"
    provenance_work.mkdir()
    with pytest.raises(RecoveryError) as provenance_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="provenance-work",
            work_root=provenance_work,
        )
    assert provenance_error.value.reason == "path_overlap"
    assert not (backup_root / "provenance-work").exists()

    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="root-guards",
        work_root=work_root,
    )
    artifact_work = backup_root / "root-guards/payload/database"
    with pytest.raises(RecoveryError) as verify_error:
        verify_backup(
            backup_root=backup_root,
            backup_id="root-guards",
            work_root=artifact_work,
        )
    assert verify_error.value.reason == "path_overlap"

    nested_target = backup_root / "root-guards/restored"
    with pytest.raises(RecoveryError) as restore_error:
        restore_backup(
            backup_root=backup_root,
            backup_id="root-guards",
            target_bindings=_bindings(inventory, nested_target),
            target_root=nested_target,
            work_root=work_root,
        )
    assert restore_error.value.reason == "path_overlap"
    assert not nested_target.exists()
    assert verify_backup(
        backup_root=backup_root,
        backup_id="root-guards",
        work_root=work_root,
    ).artifact_verified

    target = tmp_path / "target"
    restore_backup(
        backup_root=backup_root,
        backup_id="root-guards",
        target_bindings=_bindings(inventory, target),
        target_root=target,
        work_root=work_root,
    )
    with pytest.raises(RecoveryError) as verify_restore_error:
        verify_restore(target_root=target, work_root=target / "data")
    assert verify_restore_error.value.reason == "path_overlap"
    connection.close()


def test_verification_rejects_mode_tampering_including_protected_keys(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="mode-tamper",
        work_root=work_root,
    )
    backed_private = backup_root / "mode-tamper/payload/keys/operator.key"
    backed_private.chmod(0o644)

    with pytest.raises(RecoveryError) as error:
        verify_backup(
            backup_root=backup_root,
            backup_id="mode-tamper",
            work_root=work_root,
        )
    assert error.value.reason in {"digest_mismatch", "mode_mismatch", "unsafe_mode"}
    connection.close()


def test_capture_and_render_require_exact_recorded_config_values(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    config_path = inventory.configs[0].captured_path
    config = json.loads(config_path.read_text())
    config["server"]["port"] = 18007
    changed_bytes = json.dumps(config, sort_keys=True).encode()
    config_path.write_bytes(changed_bytes)
    changed_config = inventory.configs[0].model_copy(update={"sha256": _digest(changed_bytes)})
    drifted = inventory.model_copy(update={"configs": (changed_config,)})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    with pytest.raises(RecoveryError) as capture_error:
        create_backup(
            drifted,
            backup_root=backup_root,
            backup_id="binding-drift",
            work_root=work_root,
        )
    assert capture_error.value.reason == "binding_mismatch"
    assert not (backup_root / "binding-drift/COMPLETE").exists()

    config["server"]["port"] = 8007
    original_bytes = json.dumps(config, sort_keys=True).encode()
    config_path.write_bytes(original_bytes)
    restored_inventory = inventory.model_copy(
        update={
            "configs": (
                inventory.configs[0].model_copy(update={"sha256": _digest(original_bytes)}),
            )
        }
    )
    create_backup(
        restored_inventory,
        backup_root=backup_root,
        backup_id="render-drift",
        work_root=work_root,
    )
    payload = backup_root / "render-drift/payload"
    manifest = json.loads((payload / "manifest.json").read_text())
    captured_config = payload / manifest["resources"]["config.gateway"]["payload_path"]
    rendered_source = json.loads(captured_config.read_text())
    rendered_source["server"]["port"] = 9999
    captured_config.write_text(json.dumps(rendered_source, sort_keys=True))
    files = {entry["path"]: dict(entry) for entry in manifest["files"]}
    config_relative = manifest["resources"]["config.gateway"]["payload_path"]
    files[config_relative]["size"] = captured_config.stat().st_size
    files[config_relative]["sha256"] = _digest(captured_config.read_bytes())
    target = tmp_path / "render-target"
    target.mkdir(mode=0o700)
    target_output = recovery._open_pinned_directory(target, label="test render target")
    restore_evidence = recovery._RestoreEvidence(files={}, directories={}, absent_paths=set())
    with pytest.raises(RecoveryError) as render_error:
        recovery._render_configs(
            inventory=restored_inventory,
            manifest=manifest,
            payload=payload,
            files=files,
            target_bindings=_bindings(restored_inventory, target),
            target_root=target,
            restore_evidence=restore_evidence,
            target_output=target_output,
        )
    target_output.close(primary_error=None)
    assert render_error.value.reason == "binding_mismatch"
    connection.close()


def test_verify_rejects_symlinked_selected_id_and_uninventoried_resource_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    outside_root = tmp_path / "outside-backups"
    outside_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=outside_root,
        backup_id="linked",
        work_root=work_root,
    )
    (backup_root / "linked").symlink_to(outside_root / "linked", target_is_directory=True)
    with pytest.raises(RecoveryError) as symlink_error:
        verify_backup(
            backup_root=backup_root,
            backup_id="linked",
            work_root=work_root,
        )
    assert symlink_error.value.reason == "unsafe_path"

    original_capture = recovery._capture_payload

    def malformed_capture(*args: object, **kwargs: object) -> dict[str, object]:
        captured = original_capture(*args, **kwargs)
        resources = captured["resources"]
        assert isinstance(resources, dict)
        schema = resources["database.schema"]
        assert isinstance(schema, dict)
        schema["payload_path"] = "audits/pre-migration.json"
        return captured

    monkeypatch.setattr(recovery, "_capture_payload", malformed_capture)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="malformed-resources",
        work_root=work_root,
    )
    with pytest.raises(RecoveryError) as manifest_error:
        verify_backup(
            backup_root=backup_root,
            backup_id="malformed-resources",
            work_root=work_root,
        )
    assert manifest_error.value.reason == "invalid_manifest"
    connection.close()


@pytest.mark.parametrize("process_state", ["running", "cleanup_error", "undrained"])
def test_recorded_nonquiescent_process_facts_reject_before_reservation(
    tmp_path: Path,
    process_state: str,
) -> None:
    inventory, connection = _source(tmp_path)
    process = inventory.process_ownership[0]
    update: dict[str, object]
    if process_state == "running":
        update = {"pid": 101, "pgid": 101, "sid": 101, "running": True}
    elif process_state == "cleanup_error":
        update = {"cleanup_error": "owned group cleanup failed"}
    else:
        update = {
            "pid": 101,
            "pgid": 101,
            "sid": 101,
            "running": False,
            "group_drained": False,
            "leader_returncode": 0,
        }
    unsafe_process = process.model_copy(update=update)
    unsafe_inventory = inventory.model_copy(update={"process_ownership": (unsafe_process,)})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    with pytest.raises(RecoveryError) as error:
        create_backup(
            unsafe_inventory,
            backup_root=backup_root,
            backup_id=process_state,
            work_root=work_root,
        )
    assert error.value.reason == "runtime_not_quiescent"
    assert not (backup_root / process_state).exists()
    connection.close()


def test_attempt_cleanup_reports_cancellation_and_finalizes_marker_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    original_unlink = recovery.os.unlink
    failed_once = False

    def failing_marker_unlink(path: object, *, dir_fd: int | None = None) -> None:
        nonlocal failed_once
        if str(path).startswith(".COMPLETE-") and not failed_once:
            failed_once = True
            raise OSError("injected temporary-marker unlink failure")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(recovery.os, "unlink", failing_marker_unlink)
    result = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="alias-finalize",
        work_root=work_root,
    )
    assert result.state is PublicationState.PUBLICATION_UNCERTAIN
    assert list((backup_root / "alias-finalize").glob(".COMPLETE-*"))
    monkeypatch.setattr(recovery.os, "unlink", original_unlink)
    finalize_backup(
        backup_root=backup_root,
        backup_id="alias-finalize",
        work_root=work_root,
    )
    assert not list((backup_root / "alias-finalize").glob(".COMPLETE-*"))

    assert not list((backup_root / "alias-finalize").glob(".attempt-*"))
    original_capture = recovery._capture_payload

    def cancelled_capture(*args: object, **kwargs: object) -> dict[str, object]:
        original_capture(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(recovery, "_capture_payload", cancelled_capture)
    with pytest.raises(KeyboardInterrupt):
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="cancelled",
            work_root=work_root,
        )
    reports = list((backup_root / ".attempts").glob("*.json"))
    assert reports
    report_values = [json.loads(report.read_text()) for report in reports]
    assert any(
        report.get("backup_id") == "cancelled" and report.get("error_code") == "cancelled"
        for report in report_values
    )
    assert not (backup_root / "cancelled/COMPLETE").exists()
    connection.close()


def test_empty_attempt_cleanup_failure_remains_incomplete_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    def fail_cleanup(_parent: object, _name: str) -> None:
        raise OSError("injected empty-attempt cleanup failure")

    monkeypatch.setattr(recovery, "_remove_empty_attempt_pinned", fail_cleanup)
    with pytest.raises(RecoveryError) as error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="cleanup-failed",
            work_root=work_root,
        )
    assert error.value.reason == "publication_failed"
    assert not (backup_root / "cleanup-failed/COMPLETE").exists()
    reports = list((backup_root / ".attempts").glob("*.json"))
    assert any(
        json.loads(report.read_text()).get("stage") == "attempt_cleanup" for report in reports
    )
    connection.close()


def test_new_attempt_report_directory_is_durably_linked_from_backup_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    synced: list[tuple[int, int]] = []
    original_sync = recovery.os.fsync

    def record_sync(descriptor: int) -> None:
        metadata = recovery.os.fstat(descriptor)
        synced.append((metadata.st_dev, metadata.st_ino))
        original_sync(descriptor)

    def cancelled_capture(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise KeyboardInterrupt

    monkeypatch.setattr(recovery.os, "fsync", record_sync)
    monkeypatch.setattr(recovery, "_capture_payload", cancelled_capture)
    with pytest.raises(KeyboardInterrupt):
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="attempt-directory-sync",
            work_root=work_root,
        )
    backup_metadata = backup_root.stat()
    assert (backup_metadata.st_dev, backup_metadata.st_ino) in synced
    connection.close()


def test_target_endpoint_types_are_checked_before_reservation(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="endpoint-types",
        work_root=work_root,
    )
    target = tmp_path / "target"
    bindings = _bindings(inventory, target)
    endpoint_values = dict(bindings.endpoint_values)
    endpoint_values["gateway.server.port"] = "18007"
    wrong_type = bindings.model_copy(update={"endpoint_values": endpoint_values})

    with pytest.raises(RecoveryError) as error:
        restore_backup(
            backup_root=backup_root,
            backup_id="endpoint-types",
            target_bindings=wrong_type,
            target_root=target,
            work_root=work_root,
        )
    assert error.value.reason == "binding_mismatch"
    assert not target.exists()
    connection.close()


def test_absent_file_intent_stays_absent_through_backup_and_restore(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    absent_path = inventory.source_root / "legacy/board.db"
    resources = (
        *inventory.resources,
        SourceResourceV1(
            logical_id="legacy.board.database",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.ABSENT_FILE,
            captured_path=absent_path,
            capture_root=inventory.source_root,
        ),
    )
    inventory = inventory.model_copy(update={"resources": resources})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="absent-file",
        work_root=work_root,
    )
    manifest = json.loads((backup_root / "absent-file/payload/manifest.json").read_text())
    record = manifest["resources"]["legacy.board.database"]
    assert record["capture_kind"] == "absent_file"
    assert record["present"] is False
    assert "payload_path" not in record

    target = tmp_path / "target"
    restore_backup(
        backup_root=backup_root,
        backup_id="absent-file",
        target_bindings=_bindings(inventory, target),
        target_root=target,
        work_root=work_root,
    )
    assert not (target / "resources/legacy.board.database").exists()
    connection.close()


def test_runtime_lock_intent_records_identity_without_copying_and_restores_fresh_lock(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="runtime-lock",
        work_root=work_root,
    )
    payload = backup_root / "runtime-lock/payload"
    manifest = json.loads((payload / "manifest.json").read_text())
    record = manifest["resources"]["runtime.maintenance_lock"]
    assert record["capture_kind"] == "runtime_lock"
    assert record["device"] == inventory.maintenance_lock_identity.device
    assert record["inode"] == inventory.maintenance_lock_identity.inode
    assert "payload_path" not in record
    assert not any(entry["role"] == "runtime.maintenance_lock" for entry in manifest["files"])

    target = tmp_path / "target"
    bindings = _bindings(inventory, target)
    lock_target = target / "run/maintenance.lock"
    resource_paths = dict(bindings.resource_paths)
    resource_paths["runtime.maintenance_lock"] = lock_target
    bindings = bindings.model_copy(update={"resource_paths": resource_paths})
    restore_backup(
        backup_root=backup_root,
        backup_id="runtime-lock",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    assert lock_target.read_bytes() == b""
    assert lock_target.stat().st_mode & 0o777 == 0o600
    connection.close()


def test_runtime_lock_resource_and_all_maintenance_bindings_are_required(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    resources = tuple(
        resource
        for resource in inventory.resources
        if resource.capture_kind is not CaptureKind.RUNTIME_LOCK
    )
    bindings = tuple(
        binding
        for binding in inventory.field_bindings
        if tuple(binding.field_path) != ("recovery", "maintenance_lock_path")
    )
    incomplete = inventory.model_copy(update={"resources": resources, "field_bindings": bindings})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    with pytest.raises(RecoveryError) as error:
        create_backup(
            incomplete,
            backup_root=backup_root,
            backup_id="missing-runtime-lock",
            work_root=work_root,
        )
    assert error.value.reason == "incomplete_inventory"
    assert not (backup_root / "missing-runtime-lock").exists()
    connection.close()


def test_missing_recorded_maintenance_lock_is_not_recreated(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    inventory.maintenance_lock_path.unlink()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    with pytest.raises(RecoveryError):
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="missing-lock",
            work_root=work_root,
        )
    assert not inventory.maintenance_lock_path.exists()
    assert not (backup_root / "missing-lock/COMPLETE").exists()
    connection.close()


def test_restore_rejects_bundle_bytes_changed_after_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="post-verify-race",
        work_root=work_root,
    )
    payload = backup_root / "post-verify-race/payload"
    manifest = json.loads((payload / "manifest.json").read_text())
    tasks_path = payload / manifest["resources"]["agents.tasks"]["payload_path"]
    original_restore = recovery._restore_resources

    def mutate_then_restore(*args: object, **kwargs: object) -> object:
        tasks_path.write_bytes(b"mutated-after-verification")
        return original_restore(*args, **kwargs)

    monkeypatch.setattr(recovery, "_restore_resources", mutate_then_restore)
    target = tmp_path / "target"
    with pytest.raises(RecoveryError) as error:
        restore_backup(
            backup_root=backup_root,
            backup_id="post-verify-race",
            target_bindings=_bindings(inventory, target),
            target_root=target,
            work_root=work_root,
        )
    assert error.value.reason in {"digest_mismatch", "source_changed", "mode_mismatch"}
    assert not (target / "COMPLETE").exists()
    connection.close()


def test_verifier_derives_states_and_findings_instead_of_trusting_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    inventory = inventory.model_copy(update={"keypairs": ()})
    original_capture = recovery._capture_payload

    def lying_capture(*args: object, **kwargs: object) -> dict[str, object]:
        value = original_capture(*args, **kwargs)
        states = value["states"]
        assert isinstance(states, dict)
        states["identity_complete"] = True
        states["migration_eligible"] = True
        states["operational_restore_verified"] = True
        return value

    monkeypatch.setattr(recovery, "_capture_payload", lying_capture)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="lying-states",
        work_root=work_root,
    )

    result = verify_backup(
        backup_root=backup_root,
        backup_id="lying-states",
        work_root=work_root,
    )
    assert result.snapshot_preserved is True
    assert result.identity_complete is False
    assert result.migration_eligible is False
    assert result.operational_restore_verified is False
    assert "missing_keypair_inventory" in result.finding_codes
    connection.close()


def test_verifier_rejects_unsafe_private_restore_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    original_capture = recovery._capture_payload

    def unsafe_key_mode(*args: object, **kwargs: object) -> dict[str, object]:
        value = original_capture(*args, **kwargs)
        resources = value["resources"]
        assert isinstance(resources, dict)
        private = resources["key.operator.private"]
        assert isinstance(private, dict)
        private["restore_mode"] = 0o644
        return value

    monkeypatch.setattr(recovery, "_capture_payload", unsafe_key_mode)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="unsafe-key-mode",
        work_root=work_root,
    )
    with pytest.raises(RecoveryError) as error:
        verify_backup(
            backup_root=backup_root,
            backup_id="unsafe-key-mode",
            work_root=work_root,
        )
    assert error.value.reason in {"identity_incomplete", "invalid_manifest", "unsafe_mode"}
    connection.close()


def test_absence_and_maintenance_lock_identity_are_rechecked_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    absent_path = inventory.source_root / "legacy/board.db"
    resources = (
        *inventory.resources,
        SourceResourceV1(
            logical_id="legacy.board.database",
            kind=ResourceKind.FILE,
            capture_kind=CaptureKind.ABSENT_FILE,
            captured_path=absent_path,
            capture_root=inventory.source_root,
        ),
    )
    inventory = inventory.model_copy(update={"resources": resources})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    original_keys = recovery._add_key_resources

    def create_absent_during_capture(*args: object, **kwargs: object) -> object:
        result = original_keys(*args, **kwargs)
        absent_path.parent.mkdir(parents=True)
        absent_path.write_bytes(b"appeared")
        return result

    monkeypatch.setattr(recovery, "_add_key_resources", create_absent_during_capture)
    with pytest.raises(RecoveryError) as absent_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="absence-race",
            work_root=work_root,
        )
    assert absent_error.value.reason == "unexpected_resource"
    assert not (backup_root / "absence-race/COMPLETE").exists()
    monkeypatch.setattr(recovery, "_add_key_resources", original_keys)
    absent_path.unlink()

    original_capture = recovery._capture_payload

    def replace_lock_after_capture(*args: object, **kwargs: object) -> object:
        result = original_capture(*args, **kwargs)
        inventory.maintenance_lock_path.unlink()
        inventory.maintenance_lock_path.write_bytes(b"")
        inventory.maintenance_lock_path.chmod(0o600)
        return result

    monkeypatch.setattr(recovery, "_capture_payload", replace_lock_after_capture)
    with pytest.raises(RecoveryError) as final_lock_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="final-lock-race",
            work_root=work_root,
        )
    assert final_lock_error.value.reason == "maintenance_lock_failed"
    assert not (backup_root / "final-lock-race/COMPLETE").exists()
    monkeypatch.setattr(recovery, "_capture_payload", original_capture)
    connection.close()

    inventory, connection = _source(tmp_path / "replacement")
    inventory.maintenance_lock_path.unlink()
    inventory.maintenance_lock_path.write_bytes(b"")
    inventory.maintenance_lock_path.chmod(0o600)
    with pytest.raises(RecoveryError) as replaced_error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="replaced-lock",
            work_root=work_root,
        )
    assert replaced_error.value.reason == "maintenance_lock_identity"
    assert not (backup_root / "replaced-lock/COMPLETE").exists()
    connection.close()


def test_identity_reconciliation_rejects_captured_fingerprint_mismatch(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    operator = inventory.keypairs[0].model_copy(
        update={"expected_public_fingerprint_sha256": "0" * 64}
    )
    inventory = inventory.model_copy(update={"keypairs": (operator, *inventory.keypairs[1:])})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="fingerprint-mismatch",
        work_root=work_root,
    )
    result = verify_backup(
        backup_root=backup_root,
        backup_id="fingerprint-mismatch",
        work_root=work_root,
    )
    assert result.snapshot_preserved is True
    assert result.identity_complete is False
    assert "captured_fingerprint_mismatch" in result.finding_codes
    connection.close()


def test_identity_reconciliation_uses_exact_agent_id_and_key_not_display_name(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    connection.execute(
        "CREATE TABLE identity_agents "
        "(agent_id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key TEXT NOT NULL, "
        "registered_at TEXT NOT NULL)"
    )
    gateway_public = inventory.source_root / "keys/gateway-service.pub"
    connection.execute(
        "INSERT INTO identity_agents VALUES (?, ?, ?, ?)",
        (
            "agent-operator",
            "operator",
            _canonical_public_key(gateway_public),
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.commit()
    operator = inventory.keypairs[0].model_copy(
        update={
            "registration_requirement": RegistrationRequirement.REQUIRED,
            "captured_registration_state": CapturedRegistrationState.REGISTERED,
            "expected_registered_agent_id": "agent-operator",
        }
    )
    inventory = inventory.model_copy(update={"keypairs": (operator, *inventory.keypairs[1:])})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="wrong-registration",
        work_root=work_root,
    )
    result = verify_backup(
        backup_root=backup_root,
        backup_id="wrong-registration",
        work_root=work_root,
    )
    assert result.snapshot_preserved is True
    assert result.identity_complete is False
    assert "registered_identity_mismatch" in result.finding_codes
    connection.close()


def test_registry_malformed_and_duplicate_keys_are_findings_but_external_row_is_valid(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    connection.execute(
        "CREATE TABLE identity_agents "
        "(agent_id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key TEXT NOT NULL, "
        "registered_at TEXT NOT NULL)"
    )
    external_raw = b"x" * 32
    external_key = f"ed25519:{base64.b64encode(external_raw).decode()}"
    connection.executemany(
        "INSERT INTO identity_agents VALUES (?, ?, ?, ?)",
        (
            ("external-1", "operator", external_key, "2026-01-01T00:00:00Z"),
            ("external-2", "other", external_key, "2026-01-01T00:00:00Z"),
            ("malformed", "bad", "ed25519:not-base64%%%", "2026-01-01T00:00:00Z"),
        ),
    )
    connection.commit()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="registry-findings",
        work_root=work_root,
    )
    result = verify_backup(
        backup_root=backup_root,
        backup_id="registry-findings",
        work_root=work_root,
    )
    assert result.snapshot_preserved is True
    assert result.identity_complete is False
    assert {"duplicate_registry_key", "malformed_registry_key"}.issubset(result.finding_codes)
    connection.close()


def test_exact_required_registration_and_unrelated_external_row_are_identity_complete(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    connection.execute(
        "CREATE TABLE identity_agents "
        "(agent_id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key TEXT NOT NULL, "
        "registered_at TEXT NOT NULL)"
    )
    operator_public = inventory.source_root / "keys/operator.pub"
    external_key = f"ed25519:{base64.b64encode(b'y' * 32).decode()}"
    connection.executemany(
        "INSERT INTO identity_agents VALUES (?, ?, ?, ?)",
        (
            (
                "agent-operator",
                "A display name that is not trusted",
                _canonical_public_key(operator_public),
                "2026-01-01T00:00:00Z",
            ),
            ("external", "operator", external_key, "2026-01-01T00:00:00Z"),
        ),
    )
    connection.commit()
    operator = inventory.keypairs[0].model_copy(
        update={
            "registration_requirement": RegistrationRequirement.REQUIRED,
            "captured_registration_state": CapturedRegistrationState.REGISTERED,
            "expected_registered_agent_id": "agent-operator",
        }
    )
    inventory = inventory.model_copy(update={"keypairs": (operator, *inventory.keypairs[1:])})
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="exact-registration",
        work_root=work_root,
    )

    result = verify_backup(
        backup_root=backup_root,
        backup_id="exact-registration",
        work_root=work_root,
    )
    assert result.identity_complete is True
    connection.close()


def test_gateway_registration_is_forbidden(tmp_path: Path) -> None:
    inventory, connection = _source(tmp_path)
    connection.execute(
        "CREATE TABLE identity_agents "
        "(agent_id TEXT PRIMARY KEY, name TEXT NOT NULL, public_key TEXT NOT NULL, "
        "registered_at TEXT NOT NULL)"
    )
    gateway_public = inventory.source_root / "keys/gateway-service.pub"
    connection.execute(
        "INSERT INTO identity_agents VALUES (?, ?, ?, ?)",
        (
            "gateway-agent",
            "gateway-service",
            _canonical_public_key(gateway_public),
            "2026-01-01T00:00:00Z",
        ),
    )
    connection.commit()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="gateway-registration",
        work_root=work_root,
    )

    result = verify_backup(
        backup_root=backup_root,
        backup_id="gateway-registration",
        work_root=work_root,
    )
    assert result.identity_complete is False
    assert "forbidden_registration" in result.finding_codes
    connection.close()


def test_partial_identity_schema_and_malformed_asset_rows_preserve_snapshot(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    connection.execute("CREATE TABLE identity_agents (agent_id TEXT)")
    connection.execute(
        "INSERT INTO board_assets VALUES (?, ?, ?, ?)",
        (
            "bad-asset",
            str(inventory.source_root / "assets/bad.bin"),
            "not-an-integer",
            None,
        ),
    )
    connection.commit()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="partial-schema",
        work_root=work_root,
    )
    result = verify_backup(
        backup_root=backup_root,
        backup_id="partial-schema",
        work_root=work_root,
    )
    assert result.snapshot_preserved is True
    assert result.identity_complete is False
    assert {"identity_schema_incomplete", "malformed_asset_row"}.issubset(result.finding_codes)
    connection.close()


def test_duplicate_external_agent_ids_and_zero_public_keys_are_identity_findings(
    tmp_path: Path,
) -> None:
    inventory, connection = _source(tmp_path)
    connection.execute("CREATE TABLE identity_agents (agent_id TEXT, public_key TEXT)")
    key_one = f"ed25519:{base64.b64encode(b'a' * 32).decode()}"
    key_two = f"ed25519:{base64.b64encode(b'b' * 32).decode()}"
    zero_key = f"ed25519:{base64.b64encode(bytes(32)).decode()}"
    connection.executemany(
        "INSERT INTO identity_agents VALUES (?, ?)",
        (
            ("duplicate", key_one),
            ("duplicate", key_two),
            ("zero", zero_key),
        ),
    )
    connection.commit()
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="invalid-registry-identities",
        work_root=work_root,
    )

    result = verify_backup(
        backup_root=backup_root,
        backup_id="invalid-registry-identities",
        work_root=work_root,
    )
    assert result.identity_complete is False
    assert {"duplicate_registry_agent_id", "zero_registry_key"}.issubset(result.finding_codes)
    connection.close()


def test_post_marker_fsync_failure_is_uncertain_then_exactly_finalizable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    original = recovery.os.fsync
    marker_linked = False

    def failing_final_sync(descriptor: int) -> None:
        nonlocal marker_linked
        if (backup_root / "uncertain/COMPLETE").exists() and not marker_linked:
            marker_linked = True
            raise OSError("injected post-marker fsync failure")
        original(descriptor)

    monkeypatch.setattr(recovery.os, "fsync", failing_final_sync)
    result = create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="uncertain",
        work_root=work_root,
    )
    assert result.state is PublicationState.PUBLICATION_UNCERTAIN
    manifest_before = (backup_root / "uncertain/payload/manifest.json").read_bytes()
    marker_before = (backup_root / "uncertain/COMPLETE").read_bytes()

    monkeypatch.setattr(recovery.os, "fsync", original)
    finalized = finalize_backup(
        backup_root=backup_root,
        backup_id="uncertain",
        work_root=work_root,
    )
    assert finalized.snapshot_preserved is True
    assert (backup_root / "uncertain/payload/manifest.json").read_bytes() == manifest_before
    assert (backup_root / "uncertain/COMPLETE").read_bytes() == marker_before
    connection.close()


def test_pre_marker_failure_stays_incomplete_and_is_never_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)

    def failing_link(
        _source: object,
        _source_name: str,
        _target: object,
        _target_name: str,
    ) -> None:
        raise OSError("injected full disk")

    monkeypatch.setattr(recovery, "_link_between", failing_link)
    with pytest.raises(RecoveryError) as error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="failed",
            work_root=work_root,
        )
    assert error.value.reason == "publication_failed"
    assert not (backup_root / "failed/COMPLETE").exists()
    with pytest.raises(RecoveryError) as retry:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="failed",
            work_root=work_root,
        )
    assert retry.value.reason == "already_exists"
    connection.close()


@pytest.mark.parametrize("fault", ["disk_full", "fsync", "payload_reservation"])
def test_pre_marker_storage_failures_never_publish_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    if fault == "disk_full":

        def fail_write(_descriptor: int, _data: object) -> int:
            raise OSError(errno.ENOSPC, "injected disk full")

        monkeypatch.setattr(recovery.os, "write", fail_write)
        expected_reason = "write_failed"
    elif fault == "fsync":

        def fail_fsync(_root: Path) -> None:
            raise OSError("injected fsync failure")

        monkeypatch.setattr(recovery, "_fsync_tree", fail_fsync)
        expected_reason = "publication_failed"
    else:
        original_create = recovery._create_pinned_child

        def fail_payload(parent: object, name: str, *, mode: int) -> object:
            if name == "payload":
                raise OSError("injected payload reservation failure")
            return original_create(parent, name, mode=mode)

        monkeypatch.setattr(recovery, "_create_pinned_child", fail_payload)
        expected_reason = "publication_failed"

    with pytest.raises(RecoveryError) as error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id=fault,
            work_root=work_root,
        )
    assert error.value.reason == expected_reason
    assert not (backup_root / fault / "COMPLETE").exists()
    connection.close()


def test_restore_destination_race_preserves_racer_bytes_and_stays_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id="restore-race",
        work_root=work_root,
    )
    target = tmp_path / "target"
    original = recovery._copy_verified_bundle_file
    raced: Path | None = None

    def racing_copy(
        verified: object,
        target_output: object,
        relative_value: object,
        destination: Path,
        *,
        mode: int,
        label: str,
    ) -> bytes:
        nonlocal raced
        if raced is None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"racer")
            raced = destination
        return original(
            verified,
            target_output,
            relative_value,
            destination,
            mode=mode,
            label=label,
        )

    monkeypatch.setattr(recovery, "_copy_verified_bundle_file", racing_copy)
    with pytest.raises(RecoveryError) as error:
        restore_backup(
            backup_root=backup_root,
            backup_id="restore-race",
            target_bindings=_bindings(inventory, target),
            target_root=target,
            work_root=work_root,
        )
    assert error.value.reason == "write_failed"
    assert raced is not None
    assert raced.read_bytes() == b"racer"
    assert not (target / "COMPLETE").exists()
    connection.close()


def test_payload_reservation_never_replaces_a_racing_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    original = recovery._create_pinned_child

    def race_payload(parent: object, name: str, *, mode: int) -> object:
        if name == "payload":
            raced = parent.path / name  # type: ignore[attr-defined]
            raced.mkdir(mode=0o755)
        return original(parent, name, mode=mode)

    monkeypatch.setattr(recovery, "_create_pinned_child", race_payload)
    with pytest.raises(RecoveryError) as error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="payload-race",
            work_root=work_root,
        )
    assert error.value.reason == "already_exists"
    raced_payload = backup_root / "payload-race/payload"
    assert raced_payload.is_dir()
    assert raced_payload.stat().st_mode & 0o777 == 0o755
    assert not (backup_root / "payload-race/COMPLETE").exists()
    connection.close()


@pytest.mark.parametrize("tamper", ["mode", "digest"])
def test_restore_rejects_tampered_source_config_evidence_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    create_backup(
        inventory,
        backup_root=backup_root,
        backup_id=f"source-config-{tamper}",
        work_root=work_root,
    )
    target = tmp_path / "target"
    original = recovery._render_configs

    def tamper_source_config(**kwargs: object) -> object:
        result = original(**kwargs)  # type: ignore[arg-type]
        evidence = kwargs["restore_evidence"]
        config = inventory.configs[0]
        relative = f"source-evidence/configs/{config.logical_id}.yaml"
        path = target / relative
        if tamper == "mode":
            path.chmod(0o644)
            evidence.files[relative]["mode"] = 0o644  # type: ignore[attr-defined]
        else:
            data = path.read_bytes() + b"\n# changed\n"
            path.write_bytes(data)
            path.chmod(0o600)
            record = evidence.files[relative]  # type: ignore[attr-defined]
            record["size"] = len(data)
            record["sha256"] = recovery._sha256_bytes(data)
        return result

    monkeypatch.setattr(recovery, "_render_configs", tamper_source_config)
    with pytest.raises(RecoveryError) as error:
        restore_backup(
            backup_root=backup_root,
            backup_id=f"source-config-{tamper}",
            target_bindings=_bindings(inventory, target),
            target_root=target,
            work_root=work_root,
        )
    assert error.value.reason == "target_mismatch"
    assert not (target / "COMPLETE").exists()
    connection.close()


def test_publication_rechecks_private_root_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inventory, connection = _source(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    original = recovery._publish_marker_pinned

    def race_mode(root: object, parent: object, **kwargs: object) -> object:
        root.path.chmod(0o755)  # type: ignore[attr-defined]
        return original(root, parent, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(recovery, "_publish_marker_pinned", race_mode)
    with pytest.raises(RecoveryError) as error:
        create_backup(
            inventory,
            backup_root=backup_root,
            backup_id="mode-race",
            work_root=work_root,
        )
    assert error.value.reason == "unsafe_mode"
    assert not (backup_root / "mode-race/COMPLETE").exists()
    connection.close()


def test_pinned_directory_verify_failure_closes_acquired_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "pinned"
    directory.mkdir()
    original_open = recovery.os.open
    original_close = recovery.os.close
    acquired: int | None = None
    closed: list[int] = []

    def record_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal acquired
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == directory.name:
            acquired = descriptor
        return descriptor

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    def fail_verify(_self: object) -> None:
        raise RecoveryError("source_changed", "injected verify failure")

    monkeypatch.setattr(recovery.os, "open", record_open)
    monkeypatch.setattr(recovery.os, "close", record_close)
    monkeypatch.setattr(recovery._PinnedDirectory, "verify", fail_verify)
    with pytest.raises(RecoveryError, match="injected verify failure"):
        recovery._open_pinned_directory(directory, label="test directory")
    assert acquired is not None
    assert acquired in closed


def test_source_parent_cleanup_failure_closes_untransferred_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    original_open = recovery.os.open
    original_close = recovery.os.close
    file_descriptor: int | None = None
    file_close_attempted = False
    injected = False

    def record_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal file_descriptor
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == source.name:
            file_descriptor = descriptor
        return descriptor

    def fail_parent_close(descriptor: int) -> None:
        nonlocal file_close_attempted, injected
        if descriptor == file_descriptor:
            file_close_attempted = True
            original_close(descriptor)
            return
        original_close(descriptor)
        if not injected:
            injected = True
            raise OSError("injected parent close failure")

    monkeypatch.setattr(recovery.os, "open", record_open)
    monkeypatch.setattr(recovery.os, "close", fail_parent_close)
    with pytest.raises(RecoveryError) as error:
        recovery._open_source_file(source, label="test source")
    assert error.value.reason == "close_failed"
    assert file_close_attempted


def test_directory_cleanup_attempts_every_owned_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_path = tmp_path / "root"
    root_path.mkdir()
    root = recovery._open_pinned_directory(root_path, label="test root")
    original_verify = recovery._PinnedDirectory.verify
    original_close = recovery.os.close
    acquired: list[int] = []
    attempted: list[int] = []
    original_open = recovery.os.open
    root_verifications = 0

    def record_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        descriptor = original_open(path, flags, *args, **kwargs)
        if path in {"one", "two"}:
            acquired.append(descriptor)
        return descriptor

    def fail_root_verify(directory: object) -> None:
        nonlocal root_verifications
        if directory is root:
            root_verifications += 1
            if root_verifications == 2:
                raise RecoveryError("source_changed", "injected root verify failure")
        original_verify(directory)

    def fail_one_close(descriptor: int) -> None:
        attempted.append(descriptor)
        original_close(descriptor)
        if acquired and descriptor == acquired[-1]:
            raise OSError("injected child close failure")

    monkeypatch.setattr(recovery.os, "open", record_open)
    monkeypatch.setattr(recovery.os, "close", fail_one_close)
    monkeypatch.setattr(recovery._PinnedDirectory, "verify", fail_root_verify)
    with pytest.raises(RecoveryError, match="injected root verify failure") as error:
        recovery._ensure_directory_under(root, recovery.Path("one/two"))
    assert set(acquired).issubset(attempted)
    assert any("injected child close failure" in note for note in error.value.__notes__)
    monkeypatch.undo()
    root.close(primary_error=None)


def test_wrong_mode_attempt_directory_closes_owned_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    attempts = backup_root / ".attempts"
    attempts.mkdir()
    attempts.chmod(0o755)
    backup = recovery._open_pinned_directory(backup_root, label="backup root")
    original_open = recovery.os.open
    original_close = recovery.os.close
    attempt_descriptor: int | None = None
    closed: list[int] = []

    def record_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal attempt_descriptor
        descriptor = original_open(path, flags, *args, **kwargs)
        if path == ".attempts":
            attempt_descriptor = descriptor
        return descriptor

    def record_close(descriptor: int) -> None:
        closed.append(descriptor)
        original_close(descriptor)

    monkeypatch.setattr(recovery.os, "open", record_open)
    monkeypatch.setattr(recovery.os, "close", record_close)
    with pytest.raises(RecoveryError) as error:
        recovery._attempts_directory(backup)
    assert error.value.reason == "unsafe_mode"
    assert attempt_descriptor is not None
    assert attempt_descriptor in closed
    monkeypatch.undo()
    backup.close(primary_error=None)

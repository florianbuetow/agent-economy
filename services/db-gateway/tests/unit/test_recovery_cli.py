"""Explicit recovery command inputs, result states, and diagnostic redaction."""

from __future__ import annotations

import json

import pytest
from service_commons.recovery_contract import (
    MaintenanceLockIdentityV1,
    RecoveryInventoryV1,
    TargetBindingsV1,
)

from db_gateway_service import recovery
from db_gateway_service.recovery_cli.__main__ import main

pytestmark = pytest.mark.unit


def _inventory(tmp_path):
    inventory = RecoveryInventoryV1(
        contract_version=1,
        runtime_id="cli-test",
        source_root=tmp_path / "source",
        agents_package_root=tmp_path / "agents",
        maintenance_lock_path=tmp_path / "maintenance.lock",
        maintenance_lock_identity=MaintenanceLockIdentityV1(device=1, inode=1),
        resources=(),
        configs=(),
        field_bindings=(),
        keypairs=(),
        process_ownership=(),
    )
    path = tmp_path / "inventory.json"
    path.write_text(inventory.model_dump_json())
    return inventory, path


def _backup_args(tmp_path, action):
    return [
        "backup",
        action,
        "--backup-root",
        str(tmp_path / "backups"),
        "--backup-id",
        "test-backup",
        "--work-root",
        str(tmp_path / "work"),
    ]


def test_create_passes_typed_inventory_and_explicit_paths(tmp_path, monkeypatch, capsys):
    inventory, inventory_path = _inventory(tmp_path)
    calls = []

    def create(value, **kwargs):
        calls.append((value, kwargs))
        return recovery.PublicationResult(
            state=recovery.PublicationState.COMPLETE,
            location=tmp_path / "backups" / "test-backup",
            manifest_sha256="a" * 64,
        )

    monkeypatch.setattr(recovery, "create_backup", create)
    assert main([*_backup_args(tmp_path, "create"), "--inventory", str(inventory_path)]) == 0
    assert calls == [
        (
            inventory,
            {
                "backup_root": tmp_path / "backups",
                "backup_id": "test-backup",
                "work_root": tmp_path / "work",
            },
        )
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["operation"] == "backup.create"
    assert output["result"]["state"] == "complete"


def test_invalid_inventory_is_rejected_before_engine_without_echoing_secrets(
    tmp_path, monkeypatch, capsys
):
    inventory, path = _inventory(tmp_path)
    data = inventory.model_dump(mode="json")
    data["private_material"] = "DO-NOT-PRINT-PRIVATE-MATERIAL"
    path.write_text(json.dumps(data))
    calls = []
    monkeypatch.setattr(recovery, "create_backup", lambda *args, **_kwargs: calls.append(args))

    assert main([*_backup_args(tmp_path, "create"), "--inventory", str(path)]) == 1
    captured = capsys.readouterr()
    assert "DO-NOT-PRINT-PRIVATE-MATERIAL" not in captured.out + captured.err
    assert json.loads(captured.err)["error"] == "invalid_inventory"
    assert calls == []


def test_publication_uncertain_is_nonzero_without_an_implicit_retry(tmp_path, monkeypatch, capsys):
    _value, path = _inventory(tmp_path)
    calls = []

    def create(*_args, **_kwargs):
        calls.append("create")
        return recovery.PublicationResult(
            state=recovery.PublicationState.PUBLICATION_UNCERTAIN,
            location=tmp_path / "backups" / "test-backup",
            manifest_sha256="b" * 64,
        )

    monkeypatch.setattr(recovery, "create_backup", create)
    monkeypatch.setattr(recovery, "finalize_backup", lambda **_kwargs: calls.append("finalize"))
    assert main([*_backup_args(tmp_path, "create"), "--inventory", str(path)]) == 1
    assert calls == ["create"]
    assert json.loads(capsys.readouterr().out)["result"]["state"] == "publication_uncertain"


@pytest.mark.parametrize("command", ["verify", "finalize"])
def test_verification_preserves_incomplete_recovery_states(tmp_path, monkeypatch, capsys, command):
    result = recovery.VerificationResult(
        artifact_verified=True,
        snapshot_preserved=True,
        identity_complete=False,
        migration_eligible=False,
        operational_restore_verified=False,
        posting_recovery=recovery.PostingRecoveryResult.not_applicable(),
        finding_codes=("missing_key_material",),
        manifest_sha256="c" * 64,
    )
    monkeypatch.setattr(recovery, f"{command}_backup", lambda **_kwargs: result)
    assert main(_backup_args(tmp_path, command)) == 0
    output = json.loads(capsys.readouterr().out)
    assert output == {"operation": f"backup.{command}", "result": result.model_dump(mode="json")}


def test_restore_passes_explicit_typed_bindings(tmp_path, monkeypatch, capsys):
    target_root = tmp_path / "target"
    target = TargetBindingsV1(
        contract_version=1,
        target_root=target_root,
        process_cwds={},
        agents_package_root=tmp_path / "agents",
        resource_paths={},
        endpoint_values={},
    )
    path = tmp_path / "target.json"
    path.write_text(target.model_dump_json())
    calls = []

    def restore(**kwargs):
        calls.append(kwargs)
        return recovery.PublicationResult(
            state=recovery.PublicationState.COMPLETE,
            location=target_root,
            manifest_sha256="d" * 64,
        )

    monkeypatch.setattr(recovery, "restore_backup", restore)
    arguments = _backup_args(tmp_path, "create")[2:]
    assert (
        main(
            [
                "restore",
                *arguments,
                "--target-bindings",
                str(path),
                "--target-root",
                str(target_root),
            ]
        )
        == 0
    )
    assert calls == [
        {
            "backup_root": tmp_path / "backups",
            "backup_id": "test-backup",
            "work_root": tmp_path / "work",
            "target_bindings": target,
            "target_root": target_root,
        }
    ]
    assert json.loads(capsys.readouterr().out)["operation"] == "restore"


@pytest.mark.parametrize("command", ["verify-restore", "finalize-restore"])
def test_restore_checks_do_not_require_source_paths(tmp_path, monkeypatch, capsys, command):
    calls = []
    result = recovery.VerificationResult(
        True,
        True,
        True,
        False,
        False,
        recovery.PostingRecoveryResult.not_applicable(),
        (),
        "e" * 64,
    )

    def check(**kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(recovery, command.replace("-", "_"), check)
    assert (
        main(
            [
                command,
                "--target-root",
                str(tmp_path / "target"),
                "--work-root",
                str(tmp_path / "work"),
            ]
        )
        == 0
    )
    assert calls == [{"target_root": tmp_path / "target", "work_root": tmp_path / "work"}]
    assert json.loads(capsys.readouterr().out)["result"]["operational_restore_verified"] is False


def test_engine_error_reports_reason_without_payload_details(tmp_path, monkeypatch, capsys):
    def fail(**_kwargs):
        raise recovery.RecoveryError("source_changed", "DO-NOT-PRINT-PRIVATE-MATERIAL")

    monkeypatch.setattr(recovery, "verify_backup", fail)
    assert main(_backup_args(tmp_path, "verify")) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err)["error"] == "source_changed"
    assert "DO-NOT-PRINT-PRIVATE-MATERIAL" not in captured.err + captured.out


def test_paths_are_required_instead_of_reading_ordinary_defaults():
    with pytest.raises(SystemExit) as error:
        main(["backup", "create"])
    assert error.value.code == 2

"""Explicit offline migration inputs and truthful, redacted CLI outcomes."""

from __future__ import annotations

import json

import pytest
from service_commons.recovery_contract import MaintenanceLockIdentityV1, RecoveryInventoryV1

from db_gateway_service import migration
from db_gateway_service.recovery_cli.__main__ import main

pytestmark = pytest.mark.unit


def _inputs(tmp_path):
    inventory = RecoveryInventoryV1(
        contract_version=1,
        runtime_id="migration-cli-test",
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
    arguments = [
        "migrate",
        "--inventory",
        str(path),
        "--backup-root",
        str(tmp_path / "backups"),
        "--backup-id",
        "before-v1",
        "--work-root",
        str(tmp_path / "work"),
        "--busy-timeout-ms",
        "0",
    ]
    return inventory, path, arguments


@pytest.mark.parametrize("timeout", [0, 2147483647])
def test_migration_cli_passes_explicit_inputs_and_verified_result(
    tmp_path, monkeypatch, capsys, timeout
):
    inventory, _path, arguments = _inputs(tmp_path)
    arguments[-1] = str(timeout)
    result = migration.MigrationResult(
        applied=True,
        schema_version=1,
        backup_id="before-v1",
        manifest_sha256="a" * 64,
        plan_sha256="b" * 64,
        observation="committed_v1_verified",
        evidence_path=tmp_path / "work" / "attempt" / "result.json",
    )
    calls = []

    def migrate(value, **kwargs):
        calls.append((value, kwargs))
        return result

    monkeypatch.setattr(migration, "migrate_v0_to_v1", migrate)
    assert main(arguments) == 0
    assert calls == [
        (
            inventory,
            {
                "backup_root": tmp_path / "backups",
                "backup_id": "before-v1",
                "work_root": tmp_path / "work",
                "busy_timeout_ms": timeout,
            },
        )
    ]
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "operation": "migrate",
        "result": result.model_dump(mode="json"),
    }


@pytest.mark.parametrize(
    "flag", ["--inventory", "--backup-root", "--backup-id", "--work-root", "--busy-timeout-ms"]
)
def test_migration_cli_requires_every_input(tmp_path, monkeypatch, capsys, flag):
    _inventory, _path, arguments = _inputs(tmp_path)
    index = arguments.index(flag)
    del arguments[index : index + 2]
    calls = []
    monkeypatch.setattr(migration, "migrate_v0_to_v1", lambda *a, **kw: calls.append((a, kw)))
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert f"required: {flag}" in captured.err
    assert calls == []


def test_migration_cli_rejects_invalid_inventory_without_echoing_values(
    tmp_path, monkeypatch, capsys
):
    inventory, path, arguments = _inputs(tmp_path)
    data = inventory.model_dump(mode="json")
    data["private_material"] = "DO-NOT-PRINT-PRIVATE-MATERIAL"
    path.write_text(json.dumps(data))
    calls = []
    monkeypatch.setattr(migration, "migrate_v0_to_v1", lambda *a, **kw: calls.append((a, kw)))
    assert main(arguments) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err)["error"] == "invalid_inventory"
    assert "DO-NOT-PRINT-PRIVATE-MATERIAL" not in captured.out + captured.err
    assert calls == []


@pytest.mark.parametrize(
    "observation",
    [
        "committed_v1_observed",
        "not_applied_v0_observed",
        "inconsistent_state_observed",
        "unreadable_state",
    ],
)
def test_migration_cli_commit_exception_stays_nonzero_and_never_retries(
    tmp_path, monkeypatch, capsys, observation
):
    _inventory, _path, arguments = _inputs(tmp_path)
    evidence_path = str(tmp_path / "work" / "attempt" / "result.json")
    details = {"observation": observation, "evidence_path": evidence_path}
    calls = []

    def migrate(*args, **kwargs):
        calls.append((args, kwargs))
        raise migration.MigrationError(
            "commit_outcome_uncertain", "DO-NOT-PRINT-PRIVATE-MATERIAL", details
        )

    monkeypatch.setattr(migration, "migrate_v0_to_v1", migrate)
    assert main(arguments) == 1
    assert len(calls) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "DO-NOT-PRINT-PRIVATE-MATERIAL" not in captured.err
    assert json.loads(captured.err) == {
        "error": "commit_outcome_uncertain",
        "message": "Recovery operation failed",
        "details": details,
    }


def test_migration_cli_reports_safe_admission_findings(tmp_path, monkeypatch, capsys):
    _inventory, _path, arguments = _inputs(tmp_path)
    details = {"finding_codes": ["invalid_money_storage"]}

    def migrate(*_args, **_kwargs):
        raise migration.MigrationError(
            "financial_audit_failed", "DO-NOT-PRINT-PRIVATE-MATERIAL", details
        )

    monkeypatch.setattr(migration, "migrate_v0_to_v1", migrate)
    assert main(arguments) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "DO-NOT-PRINT-PRIVATE-MATERIAL" not in captured.err
    assert json.loads(captured.err)["details"] == details


def test_migration_cli_unverified_result_cannot_report_success(tmp_path, monkeypatch, capsys):
    _inventory, _path, arguments = _inputs(tmp_path)
    result = migration.MigrationResult(
        applied=False,
        schema_version=0,
        backup_id="before-v1",
        manifest_sha256="a" * 64,
        plan_sha256="b" * 64,
        observation="not_applied_v0_observed",
        evidence_path=tmp_path / "work" / "attempt" / "result.json",
    )
    monkeypatch.setattr(migration, "migrate_v0_to_v1", lambda *_args, **_kwargs: result)
    assert main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["result"]["applied"] is False

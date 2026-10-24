"""The receipt upgrade must be an explicit, backup-bound v1-to-v2 operation."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from service_commons.recovery_contract import MaintenanceLockIdentityV1, RecoveryInventoryV1

from db_gateway_service import migration
from db_gateway_service.recovery_cli.__main__ import main

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _inputs(tmp_path: Path) -> tuple[RecoveryInventoryV1, Path, list[str]]:
    inventory = RecoveryInventoryV1(
        contract_version=1,
        runtime_id="receipt-migration-cli-test",
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
    return (
        inventory,
        path,
        [
            "migrate-v1-to-v2",
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
        ],
    )


def test_receipt_migration_cli_selects_only_the_explicit_v1_to_v2_operation(
    tmp_path, monkeypatch, capsys
):
    inventory, _path, arguments = _inputs(tmp_path)
    arguments[0] = "migrate-v1-to-v2"
    result = migration.MigrationResult(
        applied=True,
        schema_version=2,
        backup_id="before-v1",
        manifest_sha256="a" * 64,
        plan_sha256="b" * 64,
        observation="committed_v2_verified",
        evidence_path=tmp_path / "work" / "attempt" / "result.json",
    )
    calls = []

    def migrate(value, **kwargs):
        calls.append((value, kwargs))
        return result

    def reject_implicit_chain(*_args, **_kwargs):
        raise AssertionError("Receipt upgrade must never invoke the v0-to-v1 migration")

    monkeypatch.setattr(migration, "migrate_v1_to_v2", migrate, raising=False)
    monkeypatch.setattr(migration, "migrate_v0_to_v1", reject_implicit_chain)
    assert main(arguments) == 0
    assert calls == [
        (
            inventory,
            {
                "backup_root": tmp_path / "backups",
                "backup_id": "before-v1",
                "work_root": tmp_path / "work",
                "busy_timeout_ms": 0,
            },
        )
    ]
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "operation": "migrate-v1-to-v2",
        "result": result.model_dump(mode="json"),
    }


@pytest.mark.parametrize(
    "flag", ["--inventory", "--backup-root", "--backup-id", "--work-root", "--busy-timeout-ms"]
)
def test_receipt_migration_cli_requires_all_explicit_authority_inputs(
    tmp_path, monkeypatch, capsys, flag
):
    _inventory, _path, arguments = _inputs(tmp_path)
    arguments[0] = "migrate-v1-to-v2"
    index = arguments.index(flag)
    del arguments[index : index + 2]
    calls = []
    monkeypatch.setattr(
        migration, "migrate_v1_to_v2", lambda *a, **kw: calls.append((a, kw)), raising=False
    )
    with pytest.raises(SystemExit) as error:
        main(arguments)
    assert error.value.code == 2
    assert f"required: {flag}" in capsys.readouterr().err
    assert calls == []


@pytest.mark.parametrize(
    ("version", "observation"),
    [(1, "committed_v1_verified"), (2, "committed_v2_observed"), (2, "not_applied_v1_observed")],
)
def test_receipt_migration_cli_never_accepts_the_wrong_or_unverified_target(
    tmp_path, monkeypatch, capsys, version, observation
):
    _inventory, _path, arguments = _inputs(tmp_path)
    arguments[0] = "migrate-v1-to-v2"
    result = migration.MigrationResult(
        applied=True,
        schema_version=version,
        backup_id="before-v1",
        manifest_sha256="a" * 64,
        plan_sha256="b" * 64,
        observation=observation,
        evidence_path=tmp_path / "evidence.json",
    )
    monkeypatch.setattr(migration, "migrate_v1_to_v2", lambda *_a, **_kw: result, raising=False)
    assert main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["result"]["observation"] == observation


def test_receipt_migration_cli_reports_commit_uncertainty_once_without_sensitive_text(
    tmp_path, monkeypatch, capsys
):
    _inventory, _path, arguments = _inputs(tmp_path)
    arguments[0] = "migrate-v1-to-v2"
    calls = []
    details: dict[str, object] = {"observation": "committed_v2_observed"}

    def migrate(*args, **kwargs):
        calls.append((args, kwargs))
        raise migration.MigrationError("commit_outcome_uncertain", "PRIVATE-SENTINEL", details)

    monkeypatch.setattr(migration, "migrate_v1_to_v2", migrate, raising=False)
    assert main(arguments) == 1
    assert len(calls) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "PRIVATE-SENTINEL" not in output.err
    assert json.loads(output.err) == {
        "error": "commit_outcome_uncertain",
        "message": "Recovery operation failed",
        "details": details,
    }

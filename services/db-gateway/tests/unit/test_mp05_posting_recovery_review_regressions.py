"""Independent review regressions for MP-05 posting recovery ownership."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from db_gateway_service import posting_recovery_audit
from db_gateway_service.recovery import (
    RecoveryError,
    _open_pinned_directory,
    create_backup,
    verify_backup,
)
from tests.fakes.mp05_posting_recovery_fixtures import (
    FEEDER_HANDLE,
    canonical_json_bytes,
    make_fixture,
    reseal_backup,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def test_posting_ledger_directory_must_match_verified_owner_fingerprint(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path)
    different_fingerprint = "f" * 64
    assert different_fingerprint != fixture.fingerprint
    fixture.identity.rename(fixture.identity.with_name(different_fingerprint))

    config_path = next(
        config.captured_path
        for config in fixture.inventory.configs
        if config.logical_id == "agents"
    )
    roster_path = next(
        resource.captured_path
        for resource in fixture.inventory.resources
        if resource.logical_id == "agents.roster"
    )
    runtime = _open_pinned_directory(fixture.runtime_root, label="review posting runtime")
    try:
        result = posting_recovery_audit.audit_posting_recovery(
            runtime_directory=runtime,
            inventory=fixture.inventory,
            agent_config=json.loads(config_path.read_bytes()),
            roster=json.loads(roster_path.read_bytes()),
            captured_keypairs={FEEDER_HANDLE: {"fingerprint": fixture.fingerprint}},
            identity_findings=(),
        )
    finally:
        runtime.close(primary_error=None)
        fixture.connection.close()

    assert result.operational_ready is False
    assert result.ledgers[0].fingerprint == different_fingerprint
    assert result.ledgers[0].owner_status == "mismatch"
    assert result.ledgers[0].finding_codes == ("posting_owner_binding_mismatch",)


def test_resealed_artifact_rejects_invalid_posting_identity_directory_name(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path)
    backup_root = tmp_path / "backups"
    backup_root.mkdir(mode=0o700)
    work_root = tmp_path / "work"
    work_root.mkdir(mode=0o700)
    backup_id = "invalid-posting-identity-name"
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id=backup_id,
        work_root=work_root,
    )

    artifact = backup_root / backup_id
    payload = artifact / "payload"
    manifest_path = payload / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    runtime_relative = manifest["resources"]["agents.runtime_state"]["payload_path"]
    runtime = payload / runtime_relative
    old_component = f"identities/{fixture.fingerprint}/"
    invalid_component = "identities/not-a-fingerprint/"
    (runtime / "identities" / fixture.fingerprint).rename(
        runtime / "identities" / "not-a-fingerprint"
    )
    for record in manifest["files"]:
        path = record["path"]
        if old_component in path:
            record["path"] = path.replace(old_component, invalid_component, 1)
    manifest_path.write_bytes(canonical_json_bytes(manifest))
    reseal_backup(artifact)

    try:
        with pytest.raises(RecoveryError) as raised:
            verify_backup(
                backup_root=backup_root,
                backup_id=backup_id,
                work_root=work_root,
            )
    finally:
        fixture.connection.close()
    assert raised.value.reason in {"unsafe_bundle", "unsafe_resource", "unsafe_path"}

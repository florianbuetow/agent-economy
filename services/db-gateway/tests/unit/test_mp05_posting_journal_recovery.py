from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from service_auth.posting_journal import audit_posting_ledger

from db_gateway_service.recovery import (
    PublicationState,
    RecoveryError,
    create_backup,
    restore_backup,
    verify_backup,
    verify_restore,
)
from tests.fakes.mp05_posting_recovery_fixtures import (
    FEEDER_AGENT_ID,
    canonical_json_bytes,
    make_fixture,
    reseal_backup,
    reseal_restore,
    tree_files,
    tree_modes,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    backup = tmp_path / "backups"
    backup.mkdir(mode=0o700)
    work = tmp_path / "work"
    work.mkdir(mode=0o700)
    return backup, work


def _posting(value: object) -> dict[str, Any]:
    dumped = value.model_dump(mode="json")  # type: ignore[attr-defined]
    posting = dumped.get("posting_recovery")
    assert isinstance(posting, dict), "MP-05 capability missing: posting_recovery metadata"
    return posting


def _expected(
    *,
    fingerprint: str | None,
    ready: bool,
    state: str | None,
    owner_status: str | None = "verified",
    findings: list[str] | None = None,
) -> dict[str, object]:
    if fingerprint is None:
        return {"applicability": "not_applicable", "operational_ready": True, "ledgers": []}
    return {
        "applicability": "applicable",
        "operational_ready": ready,
        "ledgers": [
            {
                "fingerprint": fingerprint,
                "owner_status": owner_status,
                "state": state,
                "finding_codes": [] if findings is None else findings,
            }
        ],
    }


def _manifest(backup_root: Path, backup_id: str) -> dict[str, Any]:
    return json.loads((backup_root / backup_id / "payload/manifest.json").read_bytes())


def _round_trip(
    tmp_path: Path,
    *,
    state: str,
    relocated_tasks: bool = False,
) -> tuple[object, object, dict[str, Any], str]:
    fixture = make_fixture(tmp_path, state=state)
    before_bytes = tree_files(fixture.runtime_root)
    before_modes = tree_modes(fixture.runtime_root)
    backup_root, work_root = _roots(tmp_path)
    created = create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id=f"posting-{state}",
        work_root=work_root,
    )
    assert created.state is PublicationState.COMPLETE
    verified = verify_backup(
        backup_root=backup_root,
        backup_id=f"posting-{state}",
        work_root=work_root,
    )
    target = tmp_path / "target"
    bindings = fixture.bindings(target, relocated_tasks=relocated_tasks)
    restored = restore_backup(
        backup_root=backup_root,
        backup_id=f"posting-{state}",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    assert restored.state is PublicationState.COMPLETE
    restored_root = bindings.resource_paths["agents.runtime_state"]
    assert tree_files(restored_root) == before_bytes
    assert tree_modes(restored_root) == before_modes
    restored_verification = verify_restore(target_root=target, work_root=work_root)
    restore_manifest = json.loads((target / "restore-manifest.json").read_bytes())
    fixture.connection.close()
    return verified, restored_verification, restore_manifest, fixture.fingerprint


@pytest.mark.parametrize("state", ["ready", "adoption_required"])
def test_fixture_ledger_is_valid_under_pure_shared_codec(tmp_path: Path, state: str) -> None:
    fixture = make_fixture(tmp_path, state=state)
    postings = fixture.identity / "postings"
    owner = json.loads((fixture.identity / "owner.json").read_bytes())
    records = {
        path.name: path.read_bytes() for path in (postings / "records").iterdir() if path.is_file()
    }
    audit = audit_posting_ledger(
        owner_json=owner,
        source_bytes=(postings / "source.jsonl").read_bytes(),
        head_bytes=(postings / "head.json").read_bytes(),
        record_bytes=records,
        max_source_bytes=65_536,
        max_record_bytes=65_536,
        diagnostic_limit=8,
    )
    fixture.connection.close()
    assert audit.state == state
    assert audit.findings == ()


def test_existing_recovery_preserves_valid_posting_bytes_control(tmp_path: Path) -> None:
    _round_trip(tmp_path, state="ready")


def test_existing_recovery_preserves_invalid_posting_bytes_control(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    (fixture.identity / "postings/head.json").unlink()
    before = tree_files(fixture.runtime_root)
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id="invalid-preservation-control",
        work_root=work_root,
    )
    target = tmp_path / "target"
    bindings = fixture.bindings(target)
    restore_backup(
        backup_root=backup_root,
        backup_id="invalid-preservation-control",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    fixture.connection.close()
    assert tree_files(bindings.resource_paths["agents.runtime_state"]) == before


def test_backup_manifest_keeps_bundle_v1_without_posting_sidecar(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id="capture-metadata",
        work_root=work_root,
    )
    manifest = _manifest(backup_root, "capture-metadata")
    fixture.connection.close()
    assert manifest["bundle_format_version"] == 1
    assert "posting_recovery" not in manifest


def test_existing_v1_identity_without_posting_namespace_is_not_applicable(
    tmp_path: Path,
) -> None:
    fixture = make_fixture(tmp_path, namespace=False)
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id="no-postings",
        work_root=work_root,
    )
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="no-postings",
        work_root=work_root,
    )
    target = tmp_path / "target"
    restore_backup(
        backup_root=backup_root,
        backup_id="no-postings",
        target_bindings=fixture.bindings(target),
        target_root=target,
        work_root=work_root,
    )
    restored = verify_restore(target_root=target, work_root=work_root)
    fixture.connection.close()
    expected = _expected(fingerprint=None, ready=True, state=None, owner_status=None)
    assert _posting(verified) == expected
    assert _posting(restored) == expected


@pytest.mark.parametrize(
    ("state", "reported"),
    [
        ("empty", "empty"),
        ("unbound_source", "unbound_source"),
        ("ready", "ready"),
        ("adoption_required", "adoption_required"),
    ],
)
def test_valid_ledger_states_are_ready_and_restore_exact_bytes(
    tmp_path: Path, state: str, reported: str
) -> None:
    verified, restored, restore_manifest, fingerprint = _round_trip(tmp_path, state=state)
    expected = _expected(fingerprint=fingerprint, ready=True, state=reported)
    assert _posting(verified) == expected
    assert _posting(restored) == expected
    assert restore_manifest["source_verification"]["posting_recovery"] == expected


def test_relocated_task_source_does_not_change_retained_source_authority(tmp_path: Path) -> None:
    verified, restored, _manifest_value, fingerprint = _round_trip(
        tmp_path, state="ready", relocated_tasks=True
    )
    expected = _expected(fingerprint=fingerprint, ready=True, state="ready")
    assert _posting(verified) == expected
    assert _posting(restored) == expected


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_head",
        "missing_source",
        "corrupt_head",
        "corrupt_record",
    ],
)
def test_invalid_journal_is_preserved_but_not_operationally_ready(
    tmp_path: Path, mutation: str
) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    postings = fixture.identity / "postings"
    target = (
        postings
        / {
            "missing_head": "head.json",
            "missing_source": "source.jsonl",
            "corrupt_head": "head.json",
            "corrupt_record": "records/00000000000000000000.json",
        }[mutation]
    )
    if mutation.startswith("missing"):
        target.unlink()
    else:
        target.write_bytes(b"{}")
    before = tree_files(fixture.runtime_root)
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id=f"invalid-{mutation}",
        work_root=work_root,
    )
    verified = verify_backup(
        backup_root=backup_root,
        backup_id=f"invalid-{mutation}",
        work_root=work_root,
    )
    assert "posting_journal_invalid" in verified.finding_codes
    target_root = tmp_path / "target"
    bindings = fixture.bindings(target_root)
    restore_backup(
        backup_root=backup_root,
        backup_id=f"invalid-{mutation}",
        target_bindings=bindings,
        target_root=target_root,
        work_root=work_root,
    )
    assert tree_files(bindings.resource_paths["agents.runtime_state"]) == before
    restored = verify_restore(target_root=target_root, work_root=work_root)
    fixture.connection.close()
    expected = _expected(
        fingerprint=fixture.fingerprint,
        ready=False,
        state="invalid",
        findings=["posting_journal_invalid"],
    )
    assert _posting(verified) == expected
    assert _posting(restored) == expected


def test_independently_forged_owner_is_preserved_as_binding_mismatch(tmp_path: Path) -> None:
    fixture = make_fixture(
        tmp_path,
        state="ready",
        owner_updates={"registered_agent_id": "a-forged-owner"},
    )
    before = tree_files(fixture.runtime_root)
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id="forged-owner",
        work_root=work_root,
    )
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="forged-owner",
        work_root=work_root,
    )
    target = tmp_path / "target"
    bindings = fixture.bindings(target)
    restore_backup(
        backup_root=backup_root,
        backup_id="forged-owner",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    assert tree_files(bindings.resource_paths["agents.runtime_state"]) == before
    restored = verify_restore(target_root=target, work_root=work_root)
    fixture.connection.close()
    expected = _expected(
        fingerprint=fixture.fingerprint,
        ready=False,
        state="invalid",
        owner_status="mismatch",
        findings=["posting_owner_binding_mismatch"],
    )
    assert _posting(verified) == expected
    assert _posting(restored) == expected
    assert FEEDER_AGENT_ID not in json.dumps(expected)


def test_missing_independent_roster_trust_is_owner_unverifiable(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path, state="ready", roster_has_feeder=False)
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id="missing-roster-trust",
        work_root=work_root,
    )
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="missing-roster-trust",
        work_root=work_root,
    )
    fixture.connection.close()
    assert _posting(verified) == _expected(
        fingerprint=fixture.fingerprint,
        ready=False,
        state="invalid",
        owner_status="unverifiable",
        findings=["posting_owner_unverifiable"],
    )


def test_missing_registered_identity_fact_is_owner_unverifiable(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    fixture.connection.execute("DELETE FROM identity_agents WHERE agent_id=?", (FEEDER_AGENT_ID,))
    fixture.connection.commit()
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id="missing-registry-trust",
        work_root=work_root,
    )
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="missing-registry-trust",
        work_root=work_root,
    )
    fixture.connection.close()
    assert "registered_identity_mismatch" in verified.finding_codes
    assert "posting_owner_unverifiable" in verified.finding_codes
    assert _posting(verified) == _expected(
        fingerprint=fixture.fingerprint,
        ready=False,
        state="invalid",
        owner_status="unverifiable",
        findings=["posting_owner_unverifiable"],
    )


@pytest.mark.parametrize("missing", ["source_max_bytes", "max_record_bytes", "diagnostic_limit"])
def test_missing_explicit_posting_limit_blocks_readiness(tmp_path: Path, missing: str) -> None:
    fixture = make_fixture(tmp_path, state="ready", omit_limit=missing)
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id=f"missing-{missing}",
        work_root=work_root,
    )
    verified = verify_backup(
        backup_root=backup_root,
        backup_id=f"missing-{missing}",
        work_root=work_root,
    )
    fixture.connection.close()
    assert "posting_limits_invalid" in verified.finding_codes
    assert _posting(verified) == _expected(
        fingerprint=fixture.fingerprint,
        ready=False,
        state="invalid",
        findings=["posting_limits_invalid"],
    )


@pytest.mark.parametrize(
    ("relative", "mode"),
    [("postings/head.json", 0o640), ("postings", 0o750)],
    ids=["file", "directory"],
)
def test_non_private_journal_entry_is_rejected_or_reported(
    tmp_path: Path, relative: str, mode: int
) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    unsafe_entry = fixture.identity / relative
    unsafe_entry.chmod(mode)
    before = tree_files(fixture.runtime_root)
    backup_root, work_root = _roots(tmp_path)
    try:
        create_backup(
            fixture.inventory,
            backup_root=backup_root,
            backup_id="unsafe-mode",
            work_root=work_root,
        )
    except RecoveryError as error:
        assert error.reason in {"unsafe_mode", "unsafe_resource", "unsafe_path"}
        assert not (backup_root / "unsafe-mode/COMPLETE").exists()
        fixture.connection.close()
        return
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="unsafe-mode",
        work_root=work_root,
    )
    target = tmp_path / "target"
    bindings = fixture.bindings(target)
    restore_backup(
        backup_root=backup_root,
        backup_id="unsafe-mode",
        target_bindings=bindings,
        target_root=target,
        work_root=work_root,
    )
    assert tree_files(bindings.resource_paths["agents.runtime_state"]) == before
    fixture.connection.close()
    assert "posting_permissions_invalid" in verified.finding_codes


def test_linked_journal_file_is_rejected_or_preserved_as_not_ready(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    head = fixture.identity / "postings/head.json"
    linked = tmp_path / "linked-head.json"
    os.link(head, linked)
    backup_root, work_root = _roots(tmp_path)
    try:
        create_backup(
            fixture.inventory,
            backup_root=backup_root,
            backup_id="linked-head",
            work_root=work_root,
        )
    except RecoveryError as error:
        assert error.reason in {"unsafe_resource", "unsafe_path", "source_changed"}
        assert not (backup_root / "linked-head/COMPLETE").exists()
    else:
        verified = verify_backup(
            backup_root=backup_root,
            backup_id="linked-head",
            work_root=work_root,
        )
        assert "posting_file_linked" in verified.finding_codes
        assert _posting(verified)["operational_ready"] is False
    fixture.connection.close()


@pytest.mark.parametrize("surface", ["backup", "restored_target"])
def test_linked_captured_or_restored_journal_file_fails_admission(
    tmp_path: Path, surface: str
) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id=f"linked-{surface}",
        work_root=work_root,
    )
    manifest = _manifest(backup_root, f"linked-{surface}")
    runtime_record = manifest["resources"]["agents.runtime_state"]
    captured_runtime = backup_root / f"linked-{surface}/payload" / runtime_record["payload_path"]
    if surface == "backup":
        audited_root = captured_runtime
        verify = lambda: verify_backup(  # noqa: E731
            backup_root=backup_root,
            backup_id=f"linked-{surface}",
            work_root=work_root,
        )
    else:
        target = tmp_path / "target"
        bindings = fixture.bindings(target)
        restore_backup(
            backup_root=backup_root,
            backup_id=f"linked-{surface}",
            target_bindings=bindings,
            target_root=target,
            work_root=work_root,
        )
        audited_root = bindings.resource_paths["agents.runtime_state"]
        verify = lambda: verify_restore(target_root=target, work_root=work_root)  # noqa: E731
    head = audited_root / f"identities/{fixture.fingerprint}/postings/head.json"
    os.link(head, tmp_path / f"{surface}-head-alias.json")
    with pytest.raises(RecoveryError) as raised:
        verify()
    fixture.connection.close()
    assert raised.value.reason in {"unsafe_bundle", "unsafe_resource", "unsafe_path"}


def test_verify_backup_recomputes_after_validly_resealed_record_mutation(tmp_path: Path) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id="resealed-mutation",
        work_root=work_root,
    )
    artifact = backup_root / "resealed-mutation"
    manifest = _manifest(backup_root, "resealed-mutation")
    runtime_record = manifest["resources"]["agents.runtime_state"]
    runtime_payload = artifact / "payload" / runtime_record["payload_path"]
    record = runtime_payload / (
        f"identities/{fixture.fingerprint}/postings/records/00000000000000000000.json"
    )
    record.write_bytes(b"{}")
    relative = record.relative_to(artifact / "payload").as_posix()
    for file_record in manifest["files"]:
        if file_record["path"] == relative:
            file_record["size"] = 2
            file_record["sha256"] = hashlib.sha256(b"{}").hexdigest()
            break
    else:
        pytest.fail("fixture error: posting record absent from manifest files")
    (artifact / "payload/manifest.json").write_bytes(canonical_json_bytes(manifest))
    reseal_backup(artifact)
    verified = verify_backup(
        backup_root=backup_root,
        backup_id="resealed-mutation",
        work_root=work_root,
    )
    fixture.connection.close()
    assert "posting_journal_invalid" in verified.finding_codes
    assert _posting(verified)["operational_ready"] is False


def test_historical_v1_restore_without_stored_posting_metadata_recomputes_target(
    tmp_path: Path,
) -> None:
    fixture = make_fixture(tmp_path, state="ready")
    backup_root, work_root = _roots(tmp_path)
    create_backup(
        fixture.inventory,
        backup_root=backup_root,
        backup_id="historical-restore",
        work_root=work_root,
    )
    target = tmp_path / "target"
    restore_backup(
        backup_root=backup_root,
        backup_id="historical-restore",
        target_bindings=fixture.bindings(target),
        target_root=target,
        work_root=work_root,
    )
    restore_manifest_path = target / "restore-manifest.json"
    restore_manifest = json.loads(restore_manifest_path.read_bytes())
    restore_manifest["source_verification"].pop("posting_recovery", None)
    restore_manifest_path.write_bytes(canonical_json_bytes(restore_manifest))
    reseal_restore(target)
    verified = verify_restore(target_root=target, work_root=work_root)
    fixture.connection.close()
    assert _posting(verified) == _expected(
        fingerprint=fixture.fingerprint,
        ready=True,
        state="ready",
    )

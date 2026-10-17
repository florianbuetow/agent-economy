"""Artifact verification must not follow a file replaced after metadata inspection."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from db_gateway_service import recovery

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("operation", ["tree", "manifest"])
def test_metadata_to_read_replacement_never_reads_outside_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    selected = payload / "item.txt"
    selected.write_bytes(b"original")
    selected.chmod(0o600)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"external-marker")
    outside_reads = []
    replaced = False
    original_lstat = Path.lstat
    original_read_bytes = Path.read_bytes

    def inspect_then_replace(path: Path, *args, **kwargs):
        nonlocal replaced
        result = original_lstat(path, *args, **kwargs)
        if path == selected and not replaced:
            replaced = True
            path.rename(tmp_path / "original-item.txt")
            path.symlink_to(outside)
        return result

    def observe_path_read(path: Path):
        if path == selected and replaced:
            outside_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "lstat", inspect_then_replace)
    monkeypatch.setattr(Path, "read_bytes", observe_path_read)
    try:
        if operation == "tree":
            recovery._tree_digest(payload, exclude=frozenset())
        else:
            recovery._verify_manifest_files(
                payload,
                {
                    "files": [
                        {
                            "path": "item.txt",
                            "size": 8,
                            "mode": 0o600,
                            "sha256": hashlib.sha256(b"original").hexdigest(),
                        }
                    ]
                },
            )
    except recovery.RecoveryError:
        pass
    assert replaced
    assert outside_reads == []


def test_asset_audit_accepts_the_current_relative_storage_contract(tmp_path: Path) -> None:
    assets = tmp_path / "assets"
    relative = Path("task-id/asset-id/result.txt")
    target = assets / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"answer")
    findings = []
    referenced = set()
    recovery._audit_asset_entry(
        ("asset-id", str(relative), 6, hashlib.sha256(b"answer").hexdigest()),
        asset_root=assets,
        asset_source_root=tmp_path / "old-assets",
        findings=findings,
        referenced=referenced,
    )
    assert findings == []
    assert referenced == {target}


@pytest.mark.parametrize("reference", ["../outside.txt", "task/../../outside.txt", "."])
def test_relative_asset_reference_cannot_escape_the_declared_store(
    tmp_path: Path, reference: str
) -> None:
    findings = []
    recovery._audit_asset_entry(
        ("asset-id", reference, 0, None),
        asset_root=tmp_path / "assets",
        asset_source_root=tmp_path / "old-assets",
        findings=findings,
        referenced=set(),
    )
    assert findings == [{"code": "unsafe_asset_reference", "asset_id": "asset-id"}]

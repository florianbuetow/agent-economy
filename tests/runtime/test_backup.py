"""Backup and restore round trip in a temporary checkout."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import scripts.backup as backup

pytestmark = pytest.mark.unit

_FILES = {
    "services/db-gateway/data/economy.db": b"db-bytes\x00\x01",
    "services/db-gateway/data/economy.db-wal": b"wal-bytes",
    "services/db-gateway/data/economy.db-shm": b"shm-bytes",
    "data/keys/feeder.key": b"private-key",
    "data/keys/feeder.pub": b"public-key",
    "services/task-board/data/assets/t1/a1.bin": b"asset",
    "data/agent-runtime/feeder/intents.json": b"{}",
    "services/court/.env": b"LMSTUDIO_API_KEY=abc\n",
}


def _populate(root: Path, files: dict[str, bytes]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_round_trip_restores_identical_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _populate(source, _FILES)
    before = _snapshot(source)
    archive = tmp_path / "out" / "economy.tar.gz"

    assert (
        backup.main(["backup", str(archive), "--repo-root", str(source), "--no-stop"])
        == 0
    )
    assert archive.stat().st_mode & 0o777 == 0o600

    # Corrupt the checkout: change db, drop assets, add a stale wal-less state.
    (source / "services/db-gateway/data/economy.db").write_bytes(b"changed")
    (source / "services/db-gateway/data/economy.db-wal").write_bytes(b"stale wal")
    (source / "services/task-board/data/assets/t1/a1.bin").unlink()
    (source / "data/keys/extra.key").write_bytes(b"not in backup")

    assert (
        backup.main(
            ["restore", str(archive), "--yes", "--repo-root", str(source), "--no-stop"]
        )
        == 0
    )
    assert _snapshot(source) == before


def test_restore_into_fresh_checkout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _populate(source, _FILES)
    archive = tmp_path / "b.tar.gz"
    assert (
        backup.main(["backup", str(archive), "--repo-root", str(source), "--no-stop"])
        == 0
    )
    assert (
        backup.main(
            ["restore", str(archive), "--yes", "--repo-root", str(fresh), "--no-stop"]
        )
        == 0
    )
    assert _snapshot(fresh) == _snapshot(source)


def test_restore_requires_confirmation_flag(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _populate(source, _FILES)
    archive = tmp_path / "b.tar.gz"
    backup.main(["backup", str(archive), "--repo-root", str(source), "--no-stop"])
    (source / "services/db-gateway/data/economy.db").write_bytes(b"keep me")

    assert (
        backup.main(["restore", str(archive), "--repo-root", str(source), "--no-stop"])
        == 1
    )
    assert (source / "services/db-gateway/data/economy.db").read_bytes() == b"keep me"


def test_backup_fails_without_database_and_refuses_overwrite(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    archive = tmp_path / "b.tar.gz"
    assert (
        backup.main(["backup", str(archive), "--repo-root", str(empty), "--no-stop"])
        == 1
    )
    assert not archive.exists()
    assert not (tmp_path / "b.tar.gz.partial").exists()

    source = tmp_path / "source"
    _populate(source, _FILES)
    assert (
        backup.main(["backup", str(archive), "--repo-root", str(source), "--no-stop"])
        == 0
    )
    assert (
        backup.main(["backup", str(archive), "--repo-root", str(source), "--no-stop"])
        == 1
    )


def test_restore_rejects_archive_with_foreign_paths(tmp_path: Path) -> None:
    import tarfile

    evil = tmp_path / "evil.tar.gz"
    payload = tmp_path / "payload"
    payload.write_text("x")
    with tarfile.open(evil, "w:gz") as archive:
        archive.add(payload, arcname="../escape.txt")
    target = tmp_path / "target"
    target.mkdir()
    assert (
        backup.main(
            ["restore", str(evil), "--yes", "--repo-root", str(target), "--no-stop"]
        )
        == 1
    )
    assert not (tmp_path / "escape.txt").exists()

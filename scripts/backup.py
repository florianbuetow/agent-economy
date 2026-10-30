#!/usr/bin/env python3
"""Back up and restore the local economy state as one tar.gz archive.

Usage::

    uv run --project agents scripts/backup.py backup <dest.tar.gz>
    uv run --project agents scripts/backup.py restore <src.tar.gz> --yes

Archived paths (relative to the repository root):
database (with -wal/-shm), data/keys, task-board assets, data/agent-runtime,
and services/court/.env. The stack is stopped first (supervisor when units are
installed, otherwise ``just stop-all``). Nothing is started afterwards; the
commands to run are printed. The archive holds private keys: keep it private.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import scripts.supervise as supervise

DATABASE = "services/db-gateway/data/economy.db"
REQUIRED_PATHS = (DATABASE,)
BACKUP_PATHS = (
    DATABASE,
    f"{DATABASE}-wal",
    f"{DATABASE}-shm",
    "data/keys",
    "services/task-board/data/assets",
    "data/agent-runtime",
    "services/court/.env",
)


def _supervised_units(repo_root: Path) -> list[supervise.Unit]:
    platform = "darwin" if sys.platform == "darwin" else "linux"
    uv_path = shutil.which("uv")
    if uv_path is None:
        return []
    try:
        units = supervise.build_units(repo_root, uv_path)
    except FileNotFoundError:
        # Not a full checkout (no justfile or service configs): nothing can be supervised.
        return []
    return supervise.installed(units, supervise.default_unit_dir(platform), platform)


def stop_stack(repo_root: Path) -> bool:
    """Stop the stack through the supervisor if installed, otherwise ``just stop-all``."""
    platform = "darwin" if sys.platform == "darwin" else "linux"
    if _supervised_units(repo_root):
        uv_path = shutil.which("uv")
        assert uv_path is not None
        units = supervise.build_units(repo_root, uv_path)
        return (
            supervise.set_running(
                units, supervise.default_unit_dir(platform), platform, False
            )
            == 0
        )
    return (
        subprocess.run(["just", "stop-all"], cwd=repo_root, check=False).returncode == 0
    )


def start_hint(repo_root: Path) -> str:
    if _supervised_units(repo_root):
        return "uv run --project agents scripts/supervise.py start"
    return "just start-all"


def create_backup(repo_root: Path, dest: Path) -> list[str]:
    """Write the archive; return the archived relative paths."""
    for required in REQUIRED_PATHS:
        if not (repo_root / required).is_file():
            raise FileNotFoundError(f"Required file missing: {repo_root / required}")
    if dest.exists():
        raise FileExistsError(f"Destination already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    archived: list[str] = []
    try:
        with tarfile.open(partial, "w:gz") as archive:
            for relative in BACKUP_PATHS:
                source = repo_root / relative
                if not source.exists():
                    print(f"skip (absent): {relative}")
                    continue
                archive.add(source, arcname=relative)
                archived.append(relative)
        partial.chmod(0o600)
        with tarfile.open(partial, "r:gz") as check:
            if not check.getmembers():
                raise RuntimeError("Archive verification failed: no members")
        partial.rename(dest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return archived


def _allowed(name: str) -> bool:
    parts = Path(name).parts
    if Path(name).is_absolute() or ".." in parts:
        return False
    return any(name == p or name.startswith(f"{p}/") for p in BACKUP_PATHS)


def restore_backup(repo_root: Path, src: Path) -> list[str]:
    """Replace every archived path in the checkout with the archive content."""
    with tarfile.open(src, "r:gz") as archive:
        members = archive.getmembers()
        rejected = [m.name for m in members if not _allowed(m.name)]
        if rejected:
            raise ValueError(f"Archive holds unexpected paths: {rejected[:5]}")
        if DATABASE not in {m.name for m in members}:
            raise ValueError(f"Archive does not contain {DATABASE}")
        # Remove every managed path first so a stale -wal/-shm never pairs with the restored db.
        for relative in BACKUP_PATHS:
            target = repo_root / relative
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        archive.extractall(repo_root, members=members, filter="data")
    return sorted({m.name for m in members if m.name in BACKUP_PATHS})


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("backup", "restore"))
    parser.add_argument(
        "path", type=Path, help="Archive to write (backup) or read (restore)."
    )
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parent.parent
    )
    parser.add_argument(
        "--yes", action="store_true", help="Confirm that restore overwrites state."
    )
    parser.add_argument(
        "--no-stop",
        action="store_true",
        help="Skip stopping the stack (it is already stopped).",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    if args.command == "restore" and not args.yes:
        print(
            "restore overwrites the database, keys, assets, agent runtime state and court .env; "
            "re-run with --yes to confirm",
            file=sys.stderr,
        )
        return 1
    if not args.no_stop and not stop_stack(repo_root):
        print("could not stop the stack; aborting", file=sys.stderr)
        return 1
    try:
        if args.command == "backup":
            done = create_backup(repo_root, args.path.resolve())
            print(f"backed up {len(done)} items to {args.path}")
            print(f"next: {start_hint(repo_root)}")
        else:
            done = restore_backup(repo_root, args.path.resolve())
            print(f"restored {len(done)} items from {args.path}")
            print("next: just check-keys")
            print(f"then: {start_hint(repo_root)}")
    except (OSError, ValueError, RuntimeError, tarfile.TarError) as exc:
        print(f"{args.command} failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

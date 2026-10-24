"""Independent exact snapshots for MP-08 migration preservation tests."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def database_snapshot(database: Path) -> dict[str, object]:
    """Snapshot every table row, semantic object and SQLite sequence."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        objects = connection.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type,name"
        ).fetchall()
        rows: dict[str, object] = {}
        for _schema, table, kind, _columns, without_rowid, _strict in connection.execute(
            "PRAGMA table_list"
        ).fetchall():
            if kind != "table" or table.startswith("sqlite_"):
                continue
            columns = connection.execute(f'PRAGMA table_xinfo("{table}")').fetchall()  # nosec B608
            projection = ", ".join(f'typeof("{column[1]}"), "{column[1]}"' for column in columns)
            if without_rowid:
                keys = sorted((column[5], column[1]) for column in columns if column[5])
                order = ", ".join(f'"{name}"' for _position, name in keys)
            else:
                projection = f"rowid, {projection}"
                order = "rowid"
            rows[table] = connection.execute(
                f'SELECT {projection} FROM "{table}" ORDER BY {order}'  # nosec B608
            ).fetchall()
        return {
            "objects": objects,
            "rows": rows,
            "sequence": connection.execute(
                "SELECT name,seq FROM sqlite_sequence ORDER BY name"
            ).fetchall(),
        }
    finally:
        connection.close()


def storage_snapshot(source_root: Path) -> dict[str, bytes]:
    """Snapshot every existing asset and key byte without requiring either class."""
    return {
        str(path.relative_to(source_root)): path.read_bytes()
        for directory in (source_root / "assets", source_root / "keys")
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def tree_snapshot(root: Path) -> dict[str, bytes]:
    """Capture every regular file under a verified-backup root exactly."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


__all__ = ["database_snapshot", "storage_snapshot", "tree_snapshot"]

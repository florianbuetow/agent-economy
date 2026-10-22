"""Exact SQLite schema classification for startup and offline migration admission."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

SQLITE_MAX_INTEGER = (1 << 63) - 1


class SchemaState(StrEnum):
    """Every schema state recognized by the MP-06 admission boundary."""

    EMPTY = "empty"
    CURRENT_V1 = "current_v1"
    CURRENT_V2 = "current_v2"
    CURRENT_V3 = "current_v3"
    CURRENT_V4 = "current_v4"
    SUPPORTED_V0 = "supported_v0"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    FUTURE = "future"


@dataclass(frozen=True, slots=True)
class SchemaClassification:
    """Immutable schema-admission result with a deterministic semantic signature."""

    state: SchemaState
    version: int | None
    signature_sha256: str
    findings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _MetadataInspection:
    version: int | None
    valid_current: bool
    signature: object
    findings: tuple[str, ...]


def load_schema_sql(state: SchemaState) -> str:
    """Load one frozen SQL resource used to define a known schema version."""
    resource_name = {
        SchemaState.SUPPORTED_V0: "legacy_v0.sql",
        SchemaState.CURRENT_V1: "current_v1.sql",
        SchemaState.CURRENT_V2: "current_v2.sql",
        SchemaState.CURRENT_V3: "current_v3.sql",
        SchemaState.CURRENT_V4: "current_v4.sql",
    }.get(state)
    if resource_name is None:
        raise ValueError(f"Schema state has no SQL resource: {state.value}")
    sql = _load_resource(resource_name)
    if state is SchemaState.CURRENT_V3:
        return f"{sql}\n{_load_resource('task_operations_v3.sql')}"
    if state is SchemaState.CURRENT_V4:
        return (
            f"{sql}\n{_load_resource('task_operations_v3.sql')}\n"
            f"{_load_resource('publication_v4.sql')}"
        )
    return sql


def classify_schema(connection: sqlite3.Connection) -> SchemaClassification:
    """Classify a connection without mutating its schema or persistent PRAGMAs."""
    catalog = _semantic_catalog(connection)
    object_names = set(catalog)
    metadata = _inspect_metadata(connection, object_names)
    signature = _catalog_sha256({"catalog": catalog, "metadata": metadata.signature})
    if not object_names:
        return SchemaClassification(SchemaState.EMPTY, None, signature, ())

    current = _known_current_v4_catalog()
    v3 = _known_current_v3_catalog()
    v2 = _known_current_v2_catalog()
    v1 = _known_current_catalog()
    legacy_variants = _known_legacy_catalogs()
    if metadata.version is not None and metadata.version > 4:
        findings = metadata.findings or ("future_schema_version",)
        return SchemaClassification(
            SchemaState.FUTURE,
            metadata.version,
            signature,
            findings,
        )
    for expected_catalog, version, state in (
        (current, 4, SchemaState.CURRENT_V4),
        (v3, 3, SchemaState.CURRENT_V3),
        (v2, 2, SchemaState.CURRENT_V2),
        (v1, 1, SchemaState.CURRENT_V1),
    ):
        if catalog == expected_catalog and metadata.version == version and metadata.valid_current:
            return SchemaClassification(state, version, signature, ())
    if any(catalog == legacy for legacy in legacy_variants) and metadata.signature is None:
        return SchemaClassification(SchemaState.SUPPORTED_V0, 0, signature, ())

    known_older_catalogs = (*legacy_variants, v1, v2, v3)
    findings = (*_catalog_findings(catalog, current, known_older_catalogs), *metadata.findings)
    known_names = set(current)
    for legacy in known_older_catalogs:
        known_names.update(legacy)
    state = SchemaState.PARTIAL if object_names <= known_names else SchemaState.UNKNOWN
    return SchemaClassification(
        state,
        metadata.version,
        signature,
        tuple(dict.fromkeys(findings)) or ("schema_signature_mismatch",),
    )


def _load_resource(name: str) -> str:
    return (
        importlib.resources.files("db_gateway_service.schema_resources").joinpath(name).read_text()
    )


@lru_cache(maxsize=1)
def _known_current_catalog() -> dict[str, object]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V1))
        return _semantic_catalog(connection)
    finally:
        connection.close()


@lru_cache(maxsize=1)
def _known_current_v2_catalog() -> dict[str, object]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V2))
        return _semantic_catalog(connection)
    finally:
        connection.close()


@lru_cache(maxsize=1)
def _known_current_v3_catalog() -> dict[str, object]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V3))
        return _semantic_catalog(connection)
    finally:
        connection.close()


@lru_cache(maxsize=1)
def _known_current_v4_catalog() -> dict[str, object]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V4))
        return _semantic_catalog(connection)
    finally:
        connection.close()


@lru_cache(maxsize=1)
def _known_legacy_catalogs() -> tuple[dict[str, object], dict[str, object]]:
    direct = sqlite3.connect(":memory:")
    historical = sqlite3.connect(":memory:")
    try:
        direct.executescript(load_schema_sql(SchemaState.SUPPORTED_V0))
        historical.executescript(_load_resource("legacy_base.sql"))
        for statement in (
            "ALTER TABLE board_tasks ADD COLUMN dispute_id TEXT",
            "ALTER TABLE board_tasks ADD COLUMN rebuttal_deadline TEXT",
            "ALTER TABLE board_tasks ADD COLUMN rebuttal_submitted_at TEXT",
            "ALTER TABLE bank_transactions ADD COLUMN event_id INTEGER",
            "ALTER TABLE bank_escrow ADD COLUMN event_id INTEGER",
        ):
            historical.execute(statement)
        return _semantic_catalog(direct), _semantic_catalog(historical)
    finally:
        direct.close()
        historical.close()


def _semantic_catalog(connection: sqlite3.Connection) -> dict[str, object]:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' "
        "AND type IN ('table', 'index', 'view', 'trigger') "
        "ORDER BY type, name"
    ).fetchall()
    catalog: dict[str, object] = {}
    for raw_row in rows:
        row = tuple(raw_row)
        object_type = str(row[0])
        name = str(row[1])
        table_name = str(row[2])
        sql = None if row[3] is None else str(row[3])
        key = f"{object_type}:{name}"
        if object_type == "table":
            catalog[key] = _table_semantics(connection, name, sql)
        elif object_type == "index":
            catalog[key] = _index_semantics(connection, name, table_name, sql)
        else:
            catalog[key] = {
                "table": table_name,
                "definition": _tokenize_sql(sql or ""),
            }
    return catalog


def _table_semantics(
    connection: sqlite3.Connection, table_name: str, sql: str | None
) -> dict[str, object]:
    quoted = _quote_identifier(table_name)
    columns: list[dict[str, object]] = []
    for row in connection.execute(f"PRAGMA table_xinfo({quoted})").fetchall():  # nosec B608
        columns.append(
            {
                "ordinal": int(row[0]),
                "name": str(row[1]),
                "type": str(row[2]).upper(),
                "not_null": bool(row[3]),
                "default": _normalize_default(row[4]),
                "pk_ordinal": int(row[5]),
                "hidden": int(row[6]),
            }
        )

    foreign_keys: dict[int, dict[str, object]] = {}
    for row in connection.execute(f"PRAGMA foreign_key_list({quoted})").fetchall():  # nosec B608
        identifier = int(row[0])
        group = foreign_keys.setdefault(
            identifier,
            {
                "table": str(row[2]),
                "on_update": str(row[5]).upper(),
                "on_delete": str(row[6]).upper(),
                "match": str(row[7]).upper(),
                "terms": [],
            },
        )
        terms = group["terms"]
        if not isinstance(terms, list):
            raise TypeError("foreign-key terms must be a list")
        terms.append(
            {
                "sequence": int(row[1]),
                "from": str(row[3]),
                "to": None if row[4] is None else str(row[4]),
            }
        )
    semantic_foreign_keys = list(foreign_keys.values())
    semantic_foreign_keys.sort(key=_canonical_sort_key)

    indexes: list[dict[str, object]] = []
    for row in connection.execute(f"PRAGMA index_list({quoted})").fetchall():  # nosec B608
        index_name = str(row[1])
        index_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (index_name,),
        ).fetchone()
        index_sql = None if index_sql_row is None else index_sql_row[0]
        indexes.append(
            _index_semantics(
                connection,
                index_name,
                table_name,
                None if index_sql is None else str(index_sql),
            )
        )
    indexes.sort(key=_canonical_sort_key)
    return {
        "definition": _tokenize_sql(sql or ""),
        "columns": columns,
        "foreign_keys": semantic_foreign_keys,
        "indexes": indexes,
    }


def _index_semantics(
    connection: sqlite3.Connection,
    index_name: str,
    table_name: str,
    sql: str | None,
) -> dict[str, object]:
    index_rows = connection.execute(
        f"PRAGMA index_list({_quote_identifier(table_name)})"  # nosec B608
    ).fetchall()
    properties = next(row for row in index_rows if str(row[1]) == index_name)
    terms: list[dict[str, object]] = []
    for row in connection.execute(
        f"PRAGMA index_xinfo({_quote_identifier(index_name)})"  # nosec B608
    ).fetchall():
        terms.append(
            {
                "sequence": int(row[0]),
                "name": None if row[2] is None else str(row[2]),
                "descending": bool(row[3]),
                "collation": None if row[4] is None else str(row[4]).upper(),
                "key": bool(row[5]),
            }
        )
    return {
        "definition": None if sql is None else _tokenize_sql(sql),
        "table": table_name,
        "unique": bool(properties[2]),
        "origin": str(properties[3]),
        "partial": bool(properties[4]),
        "terms": terms,
    }


def _inspect_metadata(
    connection: sqlite3.Connection, object_names: set[str]
) -> _MetadataInspection:
    if "table:schema_metadata" not in object_names:
        return _MetadataInspection(None, False, None, ())
    try:
        version_rows = connection.execute(
            "SELECT typeof(version), version FROM schema_metadata"
        ).fetchall()
    except sqlite3.DatabaseError:
        return _MetadataInspection(
            None,
            False,
            "unreadable",
            ("metadata:unreadable",),
        )
    if (
        len(version_rows) == 1
        and version_rows[0][0] == "integer"
        and isinstance(version_rows[0][1], int)
        and version_rows[0][1] > 4
    ):
        future_version = int(version_rows[0][1])
        return _MetadataInspection(
            future_version,
            False,
            _json_value(tuple(version_rows[0])),
            ("metadata:future_version",),
        )
    try:
        rows = connection.execute(
            "SELECT "
            "typeof(singleton), singleton, typeof(version), version, "
            "typeof(migrated_from_version), migrated_from_version, "
            "typeof(migration_backup_id), migration_backup_id, "
            "typeof(migration_backup_manifest_sha256), migration_backup_manifest_sha256 "
            "FROM schema_metadata ORDER BY singleton"
        ).fetchall()
    except sqlite3.DatabaseError:
        return _MetadataInspection(
            None,
            False,
            "unreadable",
            ("metadata:unreadable",),
        )
    signature = [_json_value(tuple(row)) for row in rows]
    if len(rows) != 1:
        return _MetadataInspection(
            None,
            False,
            signature,
            ("metadata:row_count",),
        )
    row = tuple(rows[0])
    metadata_version = row[3] if row[2] == "integer" and isinstance(row[3], int) else None
    fresh = row[4:] == ("null", None, "null", None, "null", None)
    migrated = (
        metadata_version in {1, 2, 3, 4}
        and row[4] == "integer"
        and row[5] == metadata_version - 1
        and row[6] == "text"
        and isinstance(row[7], str)
        and _valid_backup_id(row[7])
        and row[8] == "text"
        and isinstance(row[9], str)
        and _valid_sha256(row[9])
    )
    valid = (
        row[:3] == ("integer", 1, "integer")
        and metadata_version in {1, 2, 3, 4}
        and (fresh or migrated)
    )
    return _MetadataInspection(
        metadata_version,
        valid,
        signature,
        () if valid else ("metadata:invalid_content",),
    )


def _catalog_findings(
    actual: dict[str, object],
    current: dict[str, object],
    legacy_variants: tuple[dict[str, object], ...],
) -> tuple[str, ...]:
    known_names = set(current)
    for legacy in legacy_variants:
        known_names.update(legacy)
    actual_names = set(actual)
    missing_current = sorted(set(current) - actual_names)
    unexpected = sorted(actual_names - known_names)
    changed: list[str] = []
    for name in sorted(actual_names & known_names):
        accepted = [current.get(name)]
        accepted.extend(legacy.get(name) for legacy in legacy_variants)
        if all(actual[name] != candidate for candidate in accepted):
            changed.append(name)
    findings = [*(f"missing:{name}" for name in missing_current)]
    findings.extend(f"unexpected:{name}" for name in unexpected)
    findings.extend(f"changed:{name}" for name in changed)
    return tuple(findings)


def _tokenize_sql(sql: str) -> tuple[str, ...]:
    """Return comment-free SQL tokens while preserving every quoted byte."""
    tokens: list[str] = []
    position = 0
    while position < len(sql):
        character = sql[position]
        if character.isspace():
            position += 1
            continue
        if sql.startswith("--", position):
            newline = sql.find("\n", position + 2)
            position = len(sql) if newline < 0 else newline + 1
            continue
        if sql.startswith("/*", position):
            closing = sql.find("*/", position + 2)
            if closing < 0:
                raise ValueError("unterminated SQL block comment")
            position = closing + 2
            continue
        if character in {"'", '"', "`"}:
            token, position = _quoted_token(sql, position, character)
            tokens.append(token)
            continue
        if character == "[":
            closing = sql.find("]", position + 1)
            if closing < 0:
                raise ValueError("unterminated SQL bracket identifier")
            tokens.append(sql[position : closing + 1])
            position = closing + 1
            continue
        if character.isalnum() or character in {"_", "$"}:
            end = position + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] in {"_", "$"}):
                end += 1
            tokens.append(sql[position:end].lower())
            position = end
            continue
        three_character = sql[position : position + 3]
        if three_character == "->>":
            tokens.append(three_character)
            position += 3
            continue
        two_character = sql[position : position + 2]
        if two_character in {"<=", ">=", "!=", "==", "<>", "||", "->"}:
            tokens.append(two_character)
            position += 2
            continue
        tokens.append(character)
        position += 1
    return tuple(tokens)


def _quoted_token(sql: str, start: int, delimiter: str) -> tuple[str, int]:
    position = start + 1
    while position < len(sql):
        if sql[position] == delimiter:
            if position + 1 < len(sql) and sql[position + 1] == delimiter:
                position += 2
                continue
            return sql[start : position + 1], position + 1
        position += 1
    raise ValueError("unterminated SQL quoted token")


def _normalize_default(value: object) -> tuple[str, ...] | None:
    return None if value is None else _tokenize_sql(str(value))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _catalog_sha256(catalog: object) -> str:
    encoded = json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _canonical_sort_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _valid_backup_id(value: str) -> bool:
    return (
        bool(value)
        and value not in {".", ".."}
        and not value.startswith(".")
        and "/" not in value
        and "\\" not in value
    )


def _valid_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)

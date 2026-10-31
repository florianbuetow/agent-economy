"""Exact v2 catalog admission while keeping the frozen v1 migration target."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql

pytestmark = pytest.mark.unit


def _candidate_v2_sql() -> str:
    """Independent candidate catalog from the explicit version/sidecar contract."""
    v1 = load_schema_sql(SchemaState.CURRENT_V1)
    v2 = v1.replace("CHECK (version = 1)", "CHECK (version = 2)")
    v2 = v2.replace("migrated_from_version = 0", "migrated_from_version = 1")
    v2 = v2.replace(
        "(migrated_from_version = 1",
        "(migrated_from_version IS NOT NULL\n         AND migrated_from_version = 1",
    )
    v2 = v2.replace("VALUES (1, 1, NULL, NULL, NULL)", "VALUES (1, 2, NULL, NULL, NULL)")
    resource = Path(__file__).parents[2] / "src/db_gateway_service/schema_resources/receipt_v2.sql"
    return v2 + "\n" + resource.read_text()


def test_exact_v2_catalog_is_recognized_without_mutation() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_candidate_v2_sql())
        before = tuple(connection.iterdump())
        classification = classify_schema(connection)
        assert (classification.state.value, classification.version, classification.findings) == (
            "current_v2",
            2,
            (),
        )
        assert tuple(connection.iterdump()) == before
    finally:
        connection.close()


@pytest.mark.parametrize(
    "table", ["bank_operation_receipts", "bank_operation_legs", "bank_receipt_cutover"]
)
def test_missing_v2_ownership_table_is_partial(table: str) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_candidate_v2_sql())
        connection.execute(f'DROP TABLE "{table}"')
        classification = classify_schema(connection)
        assert classification.state is SchemaState.PARTIAL
        assert classification.version == 2
    finally:
        connection.close()


def test_v2_metadata_requires_valid_explicit_backup_provenance() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_candidate_v2_sql())
        connection.execute(
            "UPDATE schema_metadata SET migrated_from_version=1, migration_backup_id='backup-1', "
            "migration_backup_manifest_sha256='not-a-hash'"
        )
        classification = classify_schema(connection)
        assert classification.state is SchemaState.PARTIAL
        assert "metadata:invalid_content" in classification.findings
    finally:
        connection.close()


def test_v2_exact_migrated_metadata_is_recognized() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_candidate_v2_sql())
        connection.execute(
            "UPDATE schema_metadata SET migrated_from_version=1, migration_backup_id=?, "
            "migration_backup_manifest_sha256=?",
            ("backup-1", "a" * 64),
        )
        assert classify_schema(connection).state.value == "current_v2"
    finally:
        connection.close()


def test_frozen_v1_target_keeps_its_exact_resource_and_classification() -> None:
    original = load_schema_sql(SchemaState.CURRENT_V1)
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(original)
        assert classify_schema(connection).state is SchemaState.CURRENT_V1
        assert hashlib.sha256(original.encode()).hexdigest() == (
            "e23b49baf0a0eae7edcbdd9d1ea37fa7db294cc4dc3c6d3d13860e3a9e6c9f21"
        )
    finally:
        connection.close()

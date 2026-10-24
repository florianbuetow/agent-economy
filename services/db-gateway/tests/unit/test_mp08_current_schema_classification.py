"""T-124 exact-current and future-version schema classification regressions."""

from __future__ import annotations

import sqlite3

import pytest

from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql

pytestmark = pytest.mark.unit


def _fresh_v5() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(load_schema_sql(SchemaState.CURRENT_V5))
    assert classify_schema(connection).state is SchemaState.CURRENT_V5
    return connection


def test_malformed_v5_metadata_is_partial_not_future() -> None:
    connection = _fresh_v5()
    try:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "UPDATE schema_metadata SET migrated_from_version=3, "
            "migration_backup_id='backup-v5', "
            "migration_backup_manifest_sha256=?",
            ("a" * 64,),
        )
        connection.execute("PRAGMA ignore_check_constraints=OFF")

        classification = classify_schema(connection)

        assert classification.state is SchemaState.PARTIAL
        assert classification.version == 5
        assert "metadata:invalid_content" in classification.findings
    finally:
        connection.close()


def test_v5_missing_catalog_object_is_partial_not_future() -> None:
    connection = _fresh_v5()
    try:
        connection.execute("DROP INDEX idx_board_effect_delivery_diagnostic")

        classification = classify_schema(connection)

        assert classification.state is SchemaState.PARTIAL
        assert classification.version == 5
        assert "missing:index:idx_board_effect_delivery_diagnostic" in classification.findings
    finally:
        connection.close()


def test_v5_unexpected_catalog_object_is_unknown_not_future() -> None:
    connection = _fresh_v5()
    try:
        connection.execute("CREATE TABLE unexpected_v5_object (value TEXT)")

        classification = classify_schema(connection)

        assert classification.state is SchemaState.UNKNOWN
        assert classification.version == 5
        assert "unexpected:table:unexpected_v5_object" in classification.findings
    finally:
        connection.close()


def test_version_six_is_future_even_with_exact_v5_catalog() -> None:
    connection = _fresh_v5()
    try:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE schema_metadata SET version=6")
        connection.execute("PRAGMA ignore_check_constraints=OFF")

        classification = classify_schema(connection)

        assert classification.state is SchemaState.FUTURE
        assert classification.version == 6
        assert "metadata:future_version" in classification.findings
    finally:
        connection.close()

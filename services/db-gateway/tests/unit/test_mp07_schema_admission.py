"""V3 recognition does not admit a v2 catalog relabeled with the new version."""

from __future__ import annotations

import sqlite3

import pytest

from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql

pytestmark = pytest.mark.unit


def test_v2_catalog_with_v3_metadata_is_partial_and_never_current() -> None:
    sql = load_schema_sql(SchemaState.CURRENT_V2)
    sql = sql.replace("CHECK (version = 2)", "CHECK (version = 3)")
    sql = sql.replace("VALUES (1, 2, NULL, NULL, NULL)", "VALUES (1, 3, NULL, NULL, NULL)")
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(sql)
        before = tuple(connection.iterdump())
        classification = classify_schema(connection)
        assert classification.state is SchemaState.PARTIAL
        assert classification.version == 3
        assert classification.findings
        assert tuple(connection.iterdump()) == before
    finally:
        connection.close()

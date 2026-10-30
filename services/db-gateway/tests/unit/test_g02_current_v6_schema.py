"""G02: a fresh Gateway boots the complete current V6 catalog and refuses V5."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from service_commons.exceptions import ServiceError

from db_gateway_service.config import clear_settings_cache
from db_gateway_service.schema import SchemaState, classify_schema, load_schema_sql
from db_gateway_service.services.db_writer import DbWriter
from tests.unit.conftest import _create_gateway_keys, _create_test_config

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

ARTIFACT_TABLES = frozenset(
    {
        "board_task_artifact_contracts",
        "board_artifact_manifests",
        "board_artifact_uploads",
        "board_artifact_upload_attempts",
        "board_legacy_artifact_observations",
        "board_artifact_archive_attempts",
        "board_artifact_cutover",
    }
)
PUBLICATION_TABLES = frozenset(
    {
        "board_task_publications",
        "board_publication_cutover",
        "board_legacy_posting_observations",
    }
)
V6_PUBLICATION_COLUMNS = frozenset(
    {
        "request_protocol_version",
        "expected_input_count",
        "input_manifest_sha256",
        "publication_contract_sha256",
    }
)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, tuple[str, bool]]:
    return {
        str(row[1]): (str(row[2]).upper(), bool(row[3]))
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()  # nosec B608
    }


def test_fresh_gateway_start_classifies_the_complete_v6_catalog_as_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "fresh.db"
    config_path = tmp_path / "config.yaml"
    _create_gateway_keys(tmp_path)
    _create_test_config(str(database), config_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))
    clear_settings_cache()
    from db_gateway_service.app import create_app

    with TestClient(create_app()) as client:
        assert client.get("/health").status_code == 200

    connection = sqlite3.connect(database)
    try:
        classification = classify_schema(connection)
        assert classification.state is SchemaState.CURRENT_V6
        assert classification.version == 6
        tables = _tables(connection)
        assert tables >= ARTIFACT_TABLES
        assert tables >= PUBLICATION_TABLES
        assert set(_columns(connection, "board_task_publications")) >= V6_PUBLICATION_COLUMNS
        assert _columns(connection, "reputation_feedback")["source"] == ("TEXT", True)
    finally:
        connection.close()


def test_runtime_schema_file_is_the_v6_catalog() -> None:
    from pathlib import Path

    runtime = Path(__file__).parents[4] / "docs" / "specifications" / "schema.sql"
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(runtime.read_text(encoding="utf-8"))
        assert classify_schema(connection).state is SchemaState.CURRENT_V6
    finally:
        connection.close()


def test_existing_v5_database_is_refused_with_the_reset_command(tmp_path: Path) -> None:
    database = tmp_path / "old-v5.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V5))
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ServiceError) as raised:
        DbWriter(
            str(database),
            5000,
            "wal",
            load_schema_sql(SchemaState.CURRENT_V6),
            current_task_board_caller_kid="gateway-task-board",
        )
    assert raised.value.error == "database_reset_required"
    assert raised.value.status_code == 503
    assert raised.value.details["version"] == 5
    assert "just stop-all" in raised.value.message
    assert str(database) in raised.value.details["reset_command"]


def test_fresh_v6_writer_bootstraps_metadata_version_six(
    tmp_path: Path,
) -> None:
    writer = DbWriter(
        db_path=str(tmp_path / "fresh-writer.db"),
        busy_timeout_ms=5000,
        journal_mode="wal",
        schema_sql=load_schema_sql(SchemaState.CURRENT_V6),
        current_task_board_caller_kid="gateway-task-board",
    )
    try:
        assert writer.connection.execute("SELECT version FROM schema_metadata").fetchone()[0] == 6
        assert _tables(writer.connection) >= ARTIFACT_TABLES
    finally:
        writer.close()


def test_input_less_protocol_one_task_binds_an_empty_input_manifest_at_acceptance(
    tmp_path: Path,
) -> None:
    from db_gateway_service.task_operation_requests import parse_task_operation_request
    from tests.conftest import make_event
    from tests.unit.test_mp09_acceptance_binding import _prepare_protocol_v1_acceptance

    writer, operations, request = _prepare_protocol_v1_acceptance(tmp_path / "empty-input.db")
    try:
        connection = writer.connection
        task_id = request.task_id
        poster_id = request.actor_id
        worker_id = str(request.argument("worker_id"))
        accepted = operations.claim_operation(request, "gateway-task-board")
        assert accepted["operation_state"] == "completed"
        assert "accepted_binding_kind" not in accepted["result"]

        publication = connection.execute(
            "SELECT operation_id,request_sha256 FROM board_task_publications WHERE task_id=?",
            (task_id,),
        ).fetchone()
        contract = connection.execute(
            "SELECT artifact_contract_version,input_provenance,accepted_binding_kind,"
            "base_request_sha256,input_manifest_id,input_manifest_sha256,"
            "publication_contract_sha256,accepted_publication_contract_sha256,"
            "publication_operation_id,input_manifest_kind,input_actor_id "
            "FROM board_task_artifact_contracts WHERE task_id=?",
            (task_id,),
        ).fetchone()
        assert contract is not None
        assert tuple(contract[:3]) == (1, "native_v6", "publication_contract")
        assert contract[3] == publication[1]
        assert contract[6] == contract[7]
        assert tuple(contract[8:]) == (publication[0], "input", poster_id)
        manifest = connection.execute(
            "SELECT kind,actor_id,state,canonical_manifest_json,manifest_sha256,"
            "publication_operation_id FROM board_artifact_manifests WHERE manifest_id=?",
            (contract[4],),
        ).fetchone()
        assert manifest is not None
        assert tuple(manifest[:3]) == ("input", poster_id, "frozen")
        assert json.loads(manifest[3])["artifacts"] == []
        assert manifest[4] == contract[5]
        assert hashlib.sha256(manifest[3].encode()).hexdigest() == manifest[4]
        assert manifest[5] == publication[0]

        writer.record_asset(
            {
                "admission": {
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "max_files_per_task": 10,
                },
                "asset_id": f"asset-{uuid4()}",
                "task_id": task_id,
                "uploader_id": worker_id,
                "filename": "deliverable.txt",
                "content_type": "text/plain",
                "size_bytes": 7,
                "storage_path": f"assets/{task_id}/deliverable.txt",
                "content_hash": "a" * 64,
                "uploaded_at": "2099-01-01T00:00:03Z",
                "event": make_event(source="board", event_type="asset.uploaded", task_id=task_id),
            },
            {"status": "accepted", "worker_id": worker_id},
        )
        submitted = operations.claim_operation(
            parse_task_operation_request(
                {
                    "protocol_version": 1,
                    "operation_id": f"op-{uuid4()}",
                    "task_id": task_id,
                    "command": "submit_deliverable",
                    "expected_status": "accepted",
                    "expected_lifecycle_version": 1,
                    "actor_kind": "agent",
                    "actor_id": worker_id,
                    "arguments": {"worker_id": worker_id},
                }
            ),
            "gateway-task-board",
        )
        assert submitted["operation_state"] == "completed"
    finally:
        writer.close()

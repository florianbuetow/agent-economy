"""Test-only canonical facts for V2 input-manifest publication ownership."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from service_auth.financial_identity import operation_id_for

from db_gateway_service.artifact_upload_requests import parse_artifact_policy
from db_gateway_service.schema import SchemaState, load_schema_sql

if TYPE_CHECKING:
    from pathlib import Path

BOARD_KID: Final[str] = "gateway-task-board"
POSTER_ID: Final[str] = "poster-input-v2"
PUBLICATION_ID: Final[str] = "op-60000000-0000-4000-8000-000000000001"
FREEZE_ID: Final[str] = "op-60000000-0000-4000-8000-000000000002"
PUBLISH_ID: Final[str] = "op-60000000-0000-4000-8000-000000000003"
TASK_ID: Final[str] = "t-60000000-0000-4000-8000-000000000001"
INPUT_MANIFEST_ID: Final[str] = "manifest-input-60000000-0000-4000-8000-000000000001"
NOW_US: Final[int] = 4_070_995_200_000_000


def canonical_json(value: object) -> str:
    """Return the approved canonical JSON representation."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_sha256(value: object) -> str:
    """Hash the approved UTF-8 canonical bytes."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def admission_payload(*, expected_input_count: int, **overrides: object) -> dict[str, object]:
    """Build the exact protocol-v2 publication admission object."""
    values: dict[str, object] = {
        "protocol_version": 2,
        "publication_operation_id": PUBLICATION_ID,
        "task_id": TASK_ID,
        "actor_kind": "agent",
        "actor_id": POSTER_ID,
        "action": "task_publication_admit",
        "resource": "POST /task-publications",
        "lock_operation_id": operation_id_for("task-lock", (TASK_ID, POSTER_ID)),
        "title": "Input-owned publication",
        "spec": "Use the exact frozen input manifest.",
        "reward": 100,
        "bidding_deadline_seconds": 60,
        "acceptance_window_seconds": 60,
        "execution_deadline_seconds": 3600,
        "review_deadline_seconds": 60,
        "expected_input_count": expected_input_count,
    }
    values.update(overrides)
    return values


def freeze_payload(input_manifest_id: str, **overrides: object) -> dict[str, object]:
    """Build the exact protocol-v2 input-freeze authorization object."""
    values: dict[str, object] = {
        "protocol_version": 2,
        "freeze_operation_id": FREEZE_ID,
        "publication_operation_id": PUBLICATION_ID,
        "task_id": TASK_ID,
        "actor_id": POSTER_ID,
        "action": "task_publication_inputs_freeze",
        "resource": f"POST /task-publications/{PUBLICATION_ID}/inputs/freeze",
        "input_manifest_id": input_manifest_id,
    }
    values.update(overrides)
    return values


def publish_payload(publication_contract_sha256: str, **overrides: object) -> dict[str, object]:
    """Build the exact separately signed protocol-v2 publish authorization."""
    values: dict[str, object] = {
        "protocol_version": 2,
        "publish_operation_id": PUBLISH_ID,
        "publication_operation_id": PUBLICATION_ID,
        "task_id": TASK_ID,
        "actor_id": POSTER_ID,
        "action": "task_publication_publish",
        "resource": "POST /tasks",
        "lock_operation_id": operation_id_for("task-lock", (TASK_ID, POSTER_ID)),
        "publication_contract_sha256": publication_contract_sha256,
    }
    values.update(overrides)
    return values


@dataclass(frozen=True, slots=True)
class PrepublicationApi:
    """Dynamically loaded V2 request and writer capability."""

    requests: Any
    writer_type: Any


def require_prepublication_api() -> PrepublicationApi:
    """Resolve the proposed API without causing collection-time import errors."""
    requests_name = "db_gateway_service.task_publication_v2_requests"
    writer_name = "db_gateway_service.services.prepublication_writer"
    assert importlib.util.find_spec(requests_name) is not None, (
        "Missing V2 capability: db_gateway_service.task_publication_v2_requests"
    )
    assert importlib.util.find_spec(writer_name) is not None, (
        "Missing V2 capability: db_gateway_service.services.prepublication_writer"
    )
    requests = importlib.import_module(requests_name)
    writer_module = importlib.import_module(writer_name)
    for symbol in (
        "parse_publication_admission_v2",
        "parse_input_manifest_freeze_v2",
        "parse_publication_publish_v2",
    ):
        assert callable(getattr(requests, symbol, None)), f"Missing V2 capability: {symbol}"
    writer_type = getattr(writer_module, "PrepublicationWriter", None)
    assert writer_type is not None, "Missing V2 capability: PrepublicationWriter"
    return PrepublicationApi(requests=requests, writer_type=writer_type)


def open_prototype_database(database_path: Path) -> sqlite3.Connection:
    """Open a fresh database with the current V6 bootstrap catalog."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V6))
        return connection
    except BaseException:
        connection.close()
        raise


def seed_poster(connection: sqlite3.Connection) -> None:
    """Seed only the registered poster required for prepublication ownership."""
    connection.execute(
        "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) VALUES (?,?,?,?)",
        (POSTER_ID, "Input Poster", "ed25519:input-poster", "2099-01-01T23:58:00Z"),
    )
    connection.commit()


def make_writer(api: PrepublicationApi, connection: sqlite3.Connection) -> Any:
    """Construct the proposed writer with explicit policy and authority."""
    policy = parse_artifact_policy(
        {
            "contract_version": 1,
            "max_file_bytes": 32,
            "max_files_per_manifest": 2,
            "max_manifest_bytes": 64,
            "reservation_ttl_seconds": 300,
            "reservation_renew_before_expiry_seconds": 60,
            "max_upload_duration_seconds": 900,
        }
    )
    return api.writer_type(
        connection,
        clock_us=lambda: NOW_US,
        artifact_policy=policy,
        current_task_board_caller_kid=BOARD_KID,
    )


def full_snapshot(connection: sqlite3.Connection) -> tuple[object, ...]:
    """Capture the exact schema and every row in every present table."""
    schema = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        ).fetchall()
    )
    tables = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    )
    data: list[tuple[str, tuple[tuple[object, ...], ...]]] = []
    for table in tables:
        rows = [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]  # nosec B608
        data.append((table, tuple(sorted(rows, key=repr))))
    return schema, tuple(data)

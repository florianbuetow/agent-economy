"""Isolated V5 graph and independent H1 legacy-asset row facts."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Final
from uuid import NAMESPACE_URL, uuid5

from db_gateway_service.schema import SchemaState, load_schema_sql

TASK_ID: Final[str] = "t-h1-legacy-asset"
POSTER_ID: Final[str] = "poster-h1-legacy-asset"
WORKER_ID: Final[str] = "worker-h1-legacy-asset"
OTHER_WORKER_ID: Final[str] = "worker-h1-legacy-other"
OPAQUE_WORKER_ID: Final[str] = "worker-\x00opaque"
ESCROW_ID: Final[str] = "escrow-h1-legacy-asset"
OTHER_ESCROW_ID: Final[str] = "escrow-h1-legacy-other"
OTHER_TASK_ID: Final[str] = "t-h1-legacy-other"
ASSET_ID: Final[str] = "asset-h1-legacy-alpha"
SECOND_ASSET_ID: Final[str] = "asset-h1-legacy-beta"
FOREIGN_CONTEXT_ASSET_ID: Final[str] = "asset-h1-legacy-foreign-context"
MIGRATION_PLAN_ID: Final[str] = "a" * 64
OBSERVED_AT_US: Final[int] = 4_070_995_200_000_000
BODY: Final[bytes] = b"historical evidence\n"
BODY_SHA256: Final[str] = hashlib.sha256(BODY).hexdigest()

OBSERVATION_COLUMNS: Final[tuple[str, ...]] = (
    "observation_id",
    "observation_contract_version",
    "migration_plan_id",
    "legacy_asset_id",
    "task_id",
    "uploader_id",
    "assigned_worker_id",
    "source_row_sha256",
    "attribution_status",
    "byte_status",
    "declared_hash_status",
    "copy_status",
    "canonical_observation_json",
    "observation_sha256",
    "observed_at_us",
    "observed_size_bytes",
    "observed_sha256",
    "generated_artifact_id",
    "generated_object_key",
)


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def source_row(*, asset_id: str = ASSET_ID) -> dict[str, object]:
    return {
        "asset_id": asset_id,
        "task_id": TASK_ID,
        "uploader_id": WORKER_ID,
        "filename": "résumé 世界.txt",
        "content_type": "text/plain",
        "size_bytes": len(BODY),
        "storage_path": f"legacy/{asset_id}.blob",
        "content_hash": None,
        "uploaded_at": "2099-01-02T00:00:00.000000Z",
    }


def migrated_identity(*, asset_id: str = ASSET_ID) -> tuple[str, str]:
    artifact_id = str(
        uuid5(
            NAMESPACE_URL,
            f"urn:agent-economy:board-artifact-v6:{MIGRATION_PLAN_ID}:{asset_id}",
        )
    )
    return artifact_id, f"live/{artifact_id[:2]}/{artifact_id}.blob"


def observation_id(*, asset_id: str = ASSET_ID) -> str:
    identity = uuid5(
        NAMESPACE_URL,
        f"urn:agent-economy:board-legacy-asset-observation:v1:{MIGRATION_PLAN_ID}:{asset_id}",
    )
    return f"obs-{identity}"


def valid_observation_row(*, asset_id: str = ASSET_ID) -> dict[str, object]:
    source = source_row(asset_id=asset_id)
    source_json = canonical_json(source)
    artifact_id, object_key = migrated_identity(asset_id=asset_id)
    observation = {
        "format_version": 1,
        "migration_plan_id": MIGRATION_PLAN_ID,
        "legacy_asset_id": asset_id,
        "source_row": source,
        "source_row_sha256": sha256_text(source_json),
        "assigned_worker_id": WORKER_ID,
        "attribution_status": "verified_worker",
        "byte_status": "verified",
        "declared_hash_status": "absent",
        "copy_status": "installed",
        "observed_size_bytes": len(BODY),
        "observed_sha256": BODY_SHA256,
        "generated_artifact_id": artifact_id,
        "generated_object_key": object_key,
        "observed_at_us": OBSERVED_AT_US,
    }
    rendered = canonical_json(observation)
    return {
        "observation_id": observation_id(asset_id=asset_id),
        "observation_contract_version": 1,
        "migration_plan_id": MIGRATION_PLAN_ID,
        "legacy_asset_id": asset_id,
        "task_id": TASK_ID,
        "uploader_id": WORKER_ID,
        "assigned_worker_id": WORKER_ID,
        "source_row_sha256": sha256_text(source_json),
        "attribution_status": "verified_worker",
        "byte_status": "verified",
        "declared_hash_status": "absent",
        "copy_status": "installed",
        "canonical_observation_json": rendered,
        "observation_sha256": sha256_text(rendered),
        "observed_at_us": OBSERVED_AT_US,
        "observed_size_bytes": len(BODY),
        "observed_sha256": BODY_SHA256,
        "generated_artifact_id": artifact_id,
        "generated_object_key": object_key,
    }


def insert_observation(connection: sqlite3.Connection, row: dict[str, object]) -> None:
    assert tuple(row) == OBSERVATION_COLUMNS
    placeholders = ",".join("?" for _ in OBSERVATION_COLUMNS)
    connection.execute(
        f"INSERT INTO board_legacy_asset_observations "  # nosec B608
        f"({','.join(OBSERVATION_COLUMNS)}) VALUES ({placeholders})",
        tuple(row[column] for column in OBSERVATION_COLUMNS),
    )


def source_snapshot(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...]:
    tables = (
        "identity_agents",
        "bank_accounts",
        "bank_escrow",
        "board_tasks",
        "board_assets",
        "board_legacy_artifact_observations",
    )
    return tuple(
        (
            table,
            tuple(
                sorted(
                    (tuple(row) for row in connection.execute(f"SELECT * FROM {table}")),  # nosec B608
                    key=repr,
                )
            ),
        )
        for table in tables
    )


def sqlite_schema_snapshot(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, str, str, str | None], ...]:
    return tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
        )
    )


def preexisting_data_snapshot(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, tuple[tuple[Any, ...], ...]], ...]:
    table_names = tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name != 'board_legacy_asset_observations' ORDER BY name"
        )
    )
    return tuple(
        (
            table_name,
            tuple(
                sorted(
                    (
                        tuple(row)
                        for row in connection.execute(f'SELECT * FROM "{table_name}"')  # nosec B608
                    ),
                    key=repr,
                )
            ),
        )
        for table_name in table_names
    )


def open_h1_source_database(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript(load_schema_sql(SchemaState.CURRENT_V6))
        for agent_id in (POSTER_ID, WORKER_ID, OTHER_WORKER_ID, OPAQUE_WORKER_ID):
            connection.execute(
                "INSERT INTO identity_agents (agent_id,name,public_key,registered_at) "
                "VALUES (?,?,?,?)",
                (agent_id, agent_id, f"ed25519:{agent_id}", "2099-01-01T23:58:00Z"),
            )
        connection.execute(
            "INSERT INTO bank_accounts (account_id,balance,created_at) VALUES (?,?,?)",
            (POSTER_ID, 0, "2099-01-01T23:58:00Z"),
        )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id,payer_account_id,amount,task_id,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (ESCROW_ID, POSTER_ID, 100, TASK_ID, "locked", "2099-01-01T23:59:00Z"),
        )
        connection.execute(
            "INSERT INTO bank_escrow "
            "(escrow_id,payer_account_id,amount,task_id,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                OTHER_ESCROW_ID,
                POSTER_ID,
                100,
                OTHER_TASK_ID,
                "locked",
                "2099-01-01T23:59:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO board_tasks "
            "(task_id,poster_id,title,spec,reward,status,bidding_deadline_seconds,"
            "deadline_seconds,review_deadline_seconds,bidding_deadline,bid_count,"
            "escrow_pending,escrow_id,worker_id,created_at) "
            "VALUES (?,?,?,?,?,'accepted',?,?,?,?,0,0,?,?,?)",
            (
                TASK_ID,
                POSTER_ID,
                "Historical asset",
                "Preserve exact bytes.",
                100,
                60,
                3600,
                60,
                "2099-01-02T00:01:00Z",
                ESCROW_ID,
                WORKER_ID,
                "2099-01-02T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO board_tasks "
            "(task_id,poster_id,title,spec,reward,status,bidding_deadline_seconds,"
            "deadline_seconds,review_deadline_seconds,bidding_deadline,bid_count,"
            "escrow_pending,escrow_id,worker_id,created_at) "
            "VALUES (?,?,?,?,?,'accepted',?,?,?,?,0,0,?,?,?)",
            (
                OTHER_TASK_ID,
                POSTER_ID,
                "Foreign historical asset context",
                "Provide independently valid foreign keys.",
                100,
                60,
                3600,
                60,
                "2099-01-02T00:01:00Z",
                OTHER_ESCROW_ID,
                OTHER_WORKER_ID,
                "2099-01-02T00:00:00Z",
            ),
        )
        for asset_id in (ASSET_ID, SECOND_ASSET_ID):
            source = source_row(asset_id=asset_id)
            connection.execute(
                "INSERT INTO board_assets "
                "(asset_id,task_id,uploader_id,filename,content_type,size_bytes,"
                "storage_path,content_hash,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
                tuple(source[column] for column in source),
            )
        foreign_source = source_row(asset_id=FOREIGN_CONTEXT_ASSET_ID)
        foreign_source["task_id"] = OTHER_TASK_ID
        foreign_source["uploader_id"] = OTHER_WORKER_ID
        connection.execute(
            "INSERT INTO board_assets "
            "(asset_id,task_id,uploader_id,filename,content_type,size_bytes,"
            "storage_path,content_hash,uploaded_at) VALUES (?,?,?,?,?,?,?,?,?)",
            tuple(foreign_source[column] for column in foreign_source),
        )
        connection.execute(
            "INSERT INTO board_legacy_artifact_observations "
            "(task_id,observation_status,canonical_observation_json,observation_sha256,"
            "observed_at_us) VALUES (?,?,?,?,?)",
            (TASK_ID, "verified", "{}", "b" * 64, OBSERVED_AT_US),
        )
        connection.commit()
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        return connection
    except BaseException:
        connection.close()
        raise


def apply_h1_schema(connection: sqlite3.Connection) -> None:
    resource = (
        Path(__file__).parents[2]
        / "src"
        / "db_gateway_service"
        / "schema_resources"
        / "legacy_assets_v6.sql"
    )
    assert resource.is_file(), "Missing H1 capability: schema_resources/legacy_assets_v6.sql"
    connection.executescript(resource.read_text(encoding="utf-8"))


__all__ = [
    "ASSET_ID",
    "BODY",
    "FOREIGN_CONTEXT_ASSET_ID",
    "MIGRATION_PLAN_ID",
    "OBSERVATION_COLUMNS",
    "OBSERVED_AT_US",
    "OPAQUE_WORKER_ID",
    "OTHER_WORKER_ID",
    "SECOND_ASSET_ID",
    "TASK_ID",
    "WORKER_ID",
    "apply_h1_schema",
    "insert_observation",
    "open_h1_source_database",
    "preexisting_data_snapshot",
    "source_snapshot",
    "sqlite_schema_snapshot",
    "valid_observation_row",
]

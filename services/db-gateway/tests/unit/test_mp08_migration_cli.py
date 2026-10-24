"""Explicit operator migration reaches V5 only with backed-up Board authority."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

from db_gateway_service import recovery
from db_gateway_service.recovery_cli.__main__ import main
from db_gateway_service.schema import SchemaState, classify_schema
from tests.fakes.gateway_mp08_legacy_history import prepare_legacy_worker_settlement_v4
from tests.fakes.gateway_mp08_migration_helpers import database_snapshot, tree_snapshot

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("caller_kid", ["gateway-task-board", "untrusted-board"])
def test_cli_runs_explicit_v5_migration_with_exact_authority(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caller_kid: str,
) -> None:
    source = prepare_legacy_worker_settlement_v4(
        tmp_path, kind="pending", caller_kid="gateway-task-board"
    )
    backup_id = "cli-before-v5"
    publication = recovery.create_backup(
        source.inventory,
        backup_root=source.backup_root,
        backup_id=backup_id,
        work_root=source.work_root,
    )
    assert publication.state is recovery.PublicationState.COMPLETE
    before = database_snapshot(source.database)
    backup_before = tree_snapshot(source.backup_root)
    inventory_path = tmp_path / "migration-inventory.json"
    inventory_path.write_text(source.inventory.model_dump_json(), encoding="utf-8")
    result = main(
        [
            "migrate-v4-to-v5",
            "--inventory",
            str(inventory_path),
            "--backup-root",
            str(source.backup_root),
            "--backup-id",
            backup_id,
            "--work-root",
            str(source.work_root),
            "--busy-timeout-ms",
            "5000",
            "--current-task-board-caller-kid",
            caller_kid,
        ]
    )
    output = capsys.readouterr()
    connection = sqlite3.connect(f"file:{source.database}?mode=ro&immutable=1", uri=True)
    try:
        state = classify_schema(connection).state
    finally:
        connection.close()
    assert tree_snapshot(source.backup_root) == backup_before
    if caller_kid == "gateway-task-board":
        assert result == 0, output
        body = json.loads(output.out)
        assert body["operation"] == "migrate-v4-to-v5"
        assert body["result"]["applied"] is True
        assert body["result"]["schema_version"] == 5
        assert body["result"]["observation"] == "committed_v5_verified"
        assert state is SchemaState.CURRENT_V5
        assert output.err == ""
    else:
        assert result == 1, output
        assert json.loads(output.err)["error"] == "current_task_board_caller_untrusted"
        assert output.out == ""
        assert state is SchemaState.CURRENT_V4
        assert database_snapshot(source.database) == before

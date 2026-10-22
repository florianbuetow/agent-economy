"""The explicit recovery CLI must expose verified publication migration."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

from db_gateway_service.recovery_cli.__main__ import main
from db_gateway_service.schema import SchemaState, classify_schema
from tests.fakes.mp05_publication_migration_history import (
    invalidate_posting_authority,
    prepare_v3_source,
    publish_verified_backup,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("valid_authority", [True, False])
def test_cli_runs_explicit_verified_v3_to_v4_migration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    valid_authority: bool,
) -> None:
    source = prepare_v3_source(tmp_path, caller_kid="gateway-board-cli-proof")
    if not valid_authority:
        source = invalidate_posting_authority(source, defect="missing_board_write")
    backup_id = "cli-publication-cutover"
    publish_verified_backup(source, backup_id=backup_id)
    inventory_path = tmp_path / "migration-inventory.json"
    inventory_path.write_text(source.inventory.model_dump_json(), encoding="utf-8")

    result = main(
        [
            "migrate-v3-to-v4",
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
        ]
    )
    output = capsys.readouterr()
    connection = sqlite3.connect(f"file:{source.database}?mode=ro&immutable=1", uri=True)
    try:
        state = classify_schema(connection).state
    finally:
        connection.close()

    if valid_authority:
        assert result == 0, output
        body = json.loads(output.out)
        assert body["operation"] == "migrate-v3-to-v4"
        assert body["result"]["applied"] is True
        assert body["result"]["schema_version"] == 4
        assert body["result"]["observation"] == "committed_v4_verified"
        assert output.err == ""
        assert state is SchemaState.CURRENT_V4
    else:
        assert result == 1, output
        assert json.loads(output.err)["error"] == "posting_authority_invalid"
        assert output.out == ""
        assert state is SchemaState.CURRENT_V3

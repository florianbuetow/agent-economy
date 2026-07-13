"""AgentDbClient response handling must not drop persisted identity_agents columns.

GAP-E12 residue audit: task-board's `TaskDbClient._normalize_task` rebuilds every
gateway response from a `_TASK_COLUMNS` whitelist, so a column present in the
database but missing from that tuple is silently dropped on the production read
path while router unit tests (which inject a fake store) stay green — `dispute_id`
was exactly this case (commit 91b649b). `AgentDbClient` has no such whitelist
constant; it names each field explicitly instead. That is the same risk spelled
differently: a schema column not spelled out in `get_by_id`'s return dict would be
silently unavailable to every caller with no failing test anywhere. This suite pins
the client's observable behavior against the canonical schema instead of a
whitelist attribute.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from identity_service.services.agent_db_client import AgentDbClient

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs" / "specifications" / "schema.sql"

_NON_COLUMN_PREFIXES = ("FOREIGN KEY", "CHECK", "PRIMARY KEY", "UNIQUE")

# list_all() deliberately omits public_key ("Public keys are omitted for brevity" —
# a documented choice in agent_db_client.py, not a bug); it is the only endpoint
# that returns fewer than the full column set.
_LIST_ALL_OMITS = frozenset({"public_key"})


def _schema_columns(table: str) -> set[str]:
    """Column names declared for `table` in the canonical schema."""
    schema = SCHEMA_PATH.read_text()
    match = re.search(rf"CREATE TABLE {table}\s*\((.*?)\n\);", schema, re.DOTALL)
    assert match is not None, f"{table} table not found in schema.sql"

    columns: set[str] = set()
    for raw_line in match.group(1).splitlines():
        line = raw_line.split("--")[0].strip()
        if not line or line.startswith(_NON_COLUMN_PREFIXES):
            continue
        name = line.split()[0].strip(",")
        if name.isidentifier():
            columns.add(name)
    return columns


def _mock_response(status_code: int, json_data: Any = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = ""
    if json_data is not None:
        response.json.return_value = json_data
    return response


@pytest.mark.unit
class TestIdentityAgentsSchemaShape:
    def test_identity_agents_has_the_expected_columns(self) -> None:
        """Pins the columns this suite reasons about; fails loudly if the schema grows."""
        assert _schema_columns("identity_agents") == {
            "agent_id",
            "name",
            "public_key",
            "registered_at",
        }


@pytest.mark.unit
class TestGetByIdCoversEveryColumn:
    """get_by_id is the full-record read; it must surface every identity_agents column."""

    async def test_get_by_id_returns_every_schema_column(self) -> None:
        client = AgentDbClient(base_url="http://gateway.invalid", timeout_seconds=1)
        row = {
            "agent_id": "a-1",
            "name": "Alice",
            "public_key": "ed25519:abc",
            "registered_at": "2026-03-01T09:00:00Z",
        }
        client._gateway._client.get = AsyncMock(return_value=_mock_response(200, row))

        result = await client.get_by_id("a-1")

        assert result is not None
        missing = _schema_columns("identity_agents") - set(result)
        assert missing == set(), (
            f"identity_agents columns absent from AgentDbClient.get_by_id's "
            f"return dict: {sorted(missing)}. They would be silently unavailable "
            "to every caller."
        )
        assert result == row

    async def test_get_by_id_drops_unknown_fields(self) -> None:
        """Behavior stays a whitelist: an unrecognized gateway field does not leak through."""
        client = AgentDbClient(base_url="http://gateway.invalid", timeout_seconds=1)
        row = {
            "agent_id": "a-1",
            "name": "Alice",
            "public_key": "ed25519:abc",
            "registered_at": "2026-03-01T09:00:00Z",
            "surprise_column": "leaked",
        }
        client._gateway._client.get = AsyncMock(return_value=_mock_response(200, row))

        result = await client.get_by_id("a-1")

        assert result is not None
        assert "surprise_column" not in result


@pytest.mark.unit
class TestListAllDeliberateOmission:
    """list_all's public_key omission is documented and must stay exactly that shape."""

    async def test_list_all_omits_only_public_key(self) -> None:
        client = AgentDbClient(base_url="http://gateway.invalid", timeout_seconds=1)
        rows = [
            {
                "agent_id": "a-1",
                "name": "Alice",
                "public_key": "ed25519:abc",
                "registered_at": "2026-03-01T09:00:00Z",
            }
        ]
        client._gateway._client.get = AsyncMock(return_value=_mock_response(200, {"agents": rows}))

        result = await client.list_all()

        assert len(result) == 1
        expected_fields = _schema_columns("identity_agents") - _LIST_ALL_OMITS
        assert set(result[0]) == expected_fields
        assert "public_key" not in result[0]
